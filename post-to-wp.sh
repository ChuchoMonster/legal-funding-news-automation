#!/bin/bash
# post-to-wp.sh — Post an LFJ blurb markdown file to WordPress and publish it
# Usage: ./post-to-wp.sh <blurb-file.md> [image-file.jpg]
# Creates the post as a draft, attaches the featured image, then publishes
# (so an article never goes live without its image).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"

if [ ! -f "$ENV_FILE" ]; then
    echo "Error: .env file not found at $ENV_FILE"
    exit 1
fi

source "$ENV_FILE"

# Primary category defaults to Premium. Set WP_CAT_OVERRIDE to publish under a
# different one (e.g. WP_CAT_OVERRIDE=86 for Public).
# Second category defaults to Commercial; set WP_CAT2_OVERRIDE to replace it
# (e.g. Consumer=238 + Public=86:  WP_CAT_OVERRIDE=238 WP_CAT2_OVERRIDE=86 ./post-to-wp.sh ...).
# NOTE: these must be set AFTER .env is sourced, which is why they are read here.
WP_CAT_PRIMARY="${WP_CAT_OVERRIDE:-$WP_CAT_PREMIUM}"
WP_CAT_SECOND="${WP_CAT2_OVERRIDE:-$WP_CAT_COMMERCIAL}"

if [ $# -lt 1 ]; then
    echo "Usage: $0 <blurb-file.md>"
    exit 1
fi

BLURB_FILE="$1"
IMAGE_FILE="${2:-}"

if [ ! -f "$BLURB_FILE" ]; then
    echo "Error: File not found: $BLURB_FILE"
    exit 1
fi

# Extract title (first ## line) and content (everything after it)
TITLE=$(grep -m1 '^## ' "$BLURB_FILE" | sed 's/^## //')
CONTENT=$(tail -n +2 "$BLURB_FILE" | sed '/^## /d')

# Convert markdown to Gutenberg blocks using Python3
BLOCK_CONTENT=$(python3 -c "
import sys, re, json

content = sys.stdin.read().strip()
paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]

blocks = []
for para in paragraphs:
    # Convert markdown links [text](url) to HTML <a> tags
    html_para = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        r'<a href=\"\2\">\1</a>',
        para
    )
    # Replace any remaining single newlines with spaces
    html_para = html_para.replace('\n', ' ')
    blocks.append('<!-- wp:paragraph -->\n<p>' + html_para + '</p>\n<!-- /wp:paragraph -->')

print('\n\n'.join(blocks))
" <<< "$CONTENT")

# Build JSON payload
JSON_PAYLOAD=$(python3 -c "
import json, sys

title = sys.argv[1]
content = sys.argv[2]
cat_premium = int(sys.argv[3])
cat_commercial = int(sys.argv[4])
tag_id = int(sys.argv[5])

payload = {
    'title': title,
    'content': content,
    'status': 'draft',
    'categories': [cat_premium, cat_commercial],
    'tags': [tag_id]
}

print(json.dumps(payload))
" "$TITLE" "$BLOCK_CONTENT" "$WP_CAT_PRIMARY" "$WP_CAT_SECOND" "$WP_TAG_LITIGATION_FUNDING")

# Post to WordPress
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${WP_SITE_URL}/wp-json/wp/v2/posts" \
    -u "${WP_USERNAME}:${WP_APP_PASSWORD}" \
    -H "Content-Type: application/json" \
    -d "$JSON_PAYLOAD")

HTTP_CODE=$(echo "$RESPONSE" | tail -1)
BODY=$(echo "$RESPONSE" | sed '$d')

if [ "$HTTP_CODE" = "201" ]; then
    POST_ID=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
    POST_LINK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['link'])")
    echo "Post created (draft)!"
    echo "  Post ID: $POST_ID"

    # Upload featured image if provided
    if [ -n "$IMAGE_FILE" ] && [ -f "$IMAGE_FILE" ]; then
        echo "Uploading featured image..."
        IMG_FILENAME=$(basename "$IMAGE_FILE")
        IMG_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${WP_SITE_URL}/wp-json/wp/v2/media" \
            -u "${WP_USERNAME}:${WP_APP_PASSWORD}" \
            -H "Content-Disposition: attachment; filename=\"${IMG_FILENAME}\"" \
            -H "Content-Type: image/jpeg" \
            --data-binary "@${IMAGE_FILE}")
        IMG_HTTP_CODE=$(echo "$IMG_RESPONSE" | tail -1)
        IMG_BODY=$(echo "$IMG_RESPONSE" | sed '$d')

        if [ "$IMG_HTTP_CODE" = "201" ]; then
            MEDIA_ID=$(echo "$IMG_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
            echo "  Image uploaded (Media ID: $MEDIA_ID)"

            # Set as featured image on the post
            UPDATE_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${WP_SITE_URL}/wp-json/wp/v2/posts/${POST_ID}" \
                -u "${WP_USERNAME}:${WP_APP_PASSWORD}" \
                -H "Content-Type: application/json" \
                -d "{\"featured_media\": ${MEDIA_ID}}")
            UPDATE_HTTP_CODE=$(echo "$UPDATE_RESPONSE" | tail -1)
            if [ "$UPDATE_HTTP_CODE" = "200" ]; then
                echo "  Featured image set!"
            else
                echo "  Warning: Failed to set featured image (HTTP $UPDATE_HTTP_CODE)"
            fi
        else
            echo "  Warning: Image upload failed (HTTP $IMG_HTTP_CODE)"
            echo "$IMG_BODY" | python3 -m json.tool 2>/dev/null || echo "$IMG_BODY"
        fi
    fi

    # Publish the post (image is now attached)
    PUB_RESPONSE=$(curl -s -w "\n%{http_code}" -X POST "${WP_SITE_URL}/wp-json/wp/v2/posts/${POST_ID}" \
        -u "${WP_USERNAME}:${WP_APP_PASSWORD}" \
        -H "Content-Type: application/json" \
        -d "{\"status\": \"publish\"}")
    PUB_HTTP_CODE=$(echo "$PUB_RESPONSE" | tail -1)
    PUB_BODY=$(echo "$PUB_RESPONSE" | sed '$d')
    if [ "$PUB_HTTP_CODE" = "200" ]; then
        PUB_LINK=$(echo "$PUB_BODY" | python3 -c "import sys,json; print(json.load(sys.stdin)['link'])")
        echo "Published!"
        echo "  URL: $PUB_LINK"
    else
        echo "  Warning: Failed to publish (HTTP $PUB_HTTP_CODE) — post left as draft"
        echo "  Draft URL: $POST_LINK"
    fi
else
    echo "Error: HTTP $HTTP_CODE"
    echo "$BODY" | python3 -m json.tool 2>/dev/null || echo "$BODY"
    exit 1
fi
