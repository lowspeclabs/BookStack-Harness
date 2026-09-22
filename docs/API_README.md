# BookStack REST API Documentation & Reference Guide

This guide provides a comprehensive overview of the BookStack REST API, including authentication, request/response formats, querying capabilities, full endpoint references, and reusable code examples.

---

## 1. Overview & Core Architecture

BookStack organizes content in a hierarchical structure:

```
[Shelves] ──► [Books] ──► [Chapters (Optional)] ──► [Pages]
```

- **Base URL Prefix**: `https://<your-bookstack-instance>/api/`
- **Specification**: OpenAPI (Swagger) compatible. Accessible directly on your instance at `/api/docs` and `/api/docs.json`.

---

## 2. Authentication & Security

### Enabling API Access
1. A user's assigned role must have the **"Access System API"** permission enabled.
2. Edit the user profile in BookStack (**Settings ➔ Users ➔ Edit User**).
3. Scroll to **API Tokens**, click **Create Token**, assign a name and expiry date.
4. Save and copy the generated **Token ID** and **Token Secret** (the secret is shown only once).

### Request Header Format
Authenticate all API requests by supplying the `Authorization` header:

```http
Authorization: Token <token_id>:<token_secret>
```

> **Note:** If logged into BookStack in an active browser session with API permissions, session-based cookies are also accepted, allowing direct browser exploration.

---

## 3. Request & Response Conventions

### Supported Content Types
- `application/json` (Standard for most POST/PUT requests)
- `application/x-www-form-urlencoded`
- `multipart/form-data` (Required for file/image uploads)

### Method Spoofing
Because standard PHP handles form-data uploads reliably on `POST`, you can perform `PUT` or `DELETE` with form-data by sending a `POST` request with the `_method` parameter:
```http
POST /api/pages/12
_method=PUT
```

### Rate Limiting
- **Default Limit**: `180` requests per minute per user.
- Configured via `.env` parameter: `API_REQUESTS_PER_MIN=180`.
- **Exceeded Limit Response**: HTTP `429 Too Many Attempts`.

### Standard Error Response Format
All errors return standard HTTP status codes (`4xx` / `5xx`) and a JSON payload:

```json
{
  "error": {
    "code": 401,
    "message": "No authorization token found on the request"
  }
}
```

---

## 4. Listing Endpoints: Pagination, Sorting & Filtering

Endpoints returning collections (e.g., `/api/books`, `/api/pages`) return a standard paginated envelope:

```json
{
  "data": [ ... ],
  "total": 42
}
```

### Query Parameters

| Parameter | Type | Default | Description | Example |
| :--- | :--- | :--- | :--- | :--- |
| `count` | Integer | 20 | Number of records to return (Max: 100) | `?count=50` |
| `offset` | Integer | 0 | Number of records to skip | `?offset=20` |
| `sort` | String | `+id` | Field to sort by. `+` = Ascending, `-` = Descending | `?sort=-updated_at` |
| `filter[<field>]` | String | - | Filter by exact match (equals) | `?filter[name]=Documentation` |
| `filter[<field>:<op>]` | String | - | Filter with explicit comparison operator | `?filter[created_at:gt]=2024-01-01` |

### Supported Filter Operators
- `eq`: Equals (e.g., `?filter[id:eq]=5`)
- `ne`: Not equals (e.g., `?filter[id:ne]=5`)
- `gt`: Greater than (e.g., `?filter[id:gt]=10`)
- `gte`: Greater than or equal to
- `lt`: Less than
- `lte`: Less than or equal to
- `like`: Substring match using SQL wildcards `%` (e.g., `?filter[name:like]=%release%`)

---

## 5. Endpoints Reference

### 5.1 Bookshelves (`/api/shelves`)
- `GET /api/shelves` - List shelves
- `POST /api/shelves` - Create a shelf (Parameters: `name`, `description`, `books` [array of IDs], `tags`, `image`)
- `GET /api/shelves/{id}` - Read shelf details
- `PUT /api/shelves/{id}` - Update shelf
- `DELETE /api/shelves/{id}` - Delete shelf

### 5.2 Books (`/api/books`)
- `GET /api/books` - List books
- `POST /api/books` - Create a book (Parameters: `name`, `description`, `tags`, `image`, `default_template_id`)
- `GET /api/books/{id}` - Read book details (includes contents tree)
- `PUT /api/books/{id}` - Update book
- `DELETE /api/books/{id}` - Delete book
- **Exports**:
  - `GET /api/books/{id}/export/html`
  - `GET /api/books/{id}/export/pdf`
  - `GET /api/books/{id}/export/plaintext`
  - `GET /api/books/{id}/export/markdown`
  - `GET /api/books/{id}/export/zip`

### 5.3 Chapters (`/api/chapters`)
- `GET /api/chapters` - List chapters
- `POST /api/chapters` - Create chapter (Parameters: `book_id`, `name`, `description`, `tags`, `priority`)
- `GET /api/chapters/{id}` - Read chapter details (includes pages)
- `PUT /api/chapters/{id}` - Update chapter
- `DELETE /api/chapters/{id}` - Delete chapter
- **Exports**:
  - `GET /api/chapters/{id}/export/html`
  - `GET /api/chapters/{id}/export/pdf`
  - `GET /api/chapters/{id}/export/plaintext`
  - `GET /api/chapters/{id}/export/markdown`
  - `GET /api/chapters/{id}/export/zip`

### 5.4 Pages (`/api/pages`)
- `GET /api/pages` - List pages
- `POST /api/pages` - Create page (Parameters: `book_id` or `chapter_id`, `name`, `html` or `markdown`, `tags`, `priority`, `changelog`)
- `GET /api/pages/{id}` - Read page content & metadata (includes raw HTML and Markdown)
- `PUT /api/pages/{id}` - Update page (`name`, `html`, `markdown`, `tags`, `priority`, `changelog`)
- `DELETE /api/pages/{id}` - Delete page
- **Exports**:
  - `GET /api/pages/{id}/export/html`
  - `GET /api/pages/{id}/export/pdf`
  - `GET /api/pages/{id}/export/plaintext`
  - `GET /api/pages/{id}/export/markdown`
  - `GET /api/pages/{id}/export/zip`

### 5.5 Global Search (`/api/search`)
- `GET /api/search?query=<query_string>&page=1&count=20`
- Supports full BookStack search syntax:
  - Exact text: `"my exact phrase"`
  - Filter by type: `{type:page}`, `{type:book}`, `{type:chapter}`, `{type:bookshelf}`
  - Filter by tag: `[tag_name]` or `[tag_name=value]`
  - Filter by author/updater: `{created_by:me}`, `{updated_by:admin}`
  - Dates: `{updated_after:2024-01-01}`

### 5.6 Attachments (`/api/attachments`)
- `GET /api/attachments` - List attachments
- `POST /api/attachments` - Create attachment (File upload via `multipart/form-data` with `uploaded_to` page ID and `file`, or link via `uploaded_to`, `name`, `link`)
- `GET /api/attachments/{id}` - Read attachment metadata
- `PUT /api/attachments/{id}` - Update attachment
- `DELETE /api/attachments/{id}` - Delete attachment

### 5.7 Image Gallery (`/api/image-gallery`)
- `GET /api/image-gallery` - List images
- `POST /api/image-gallery` - Upload image (`multipart/form-data` with `uploaded_to` page ID, `image`, `name`, `type` e.g. `gallery` or `drawio`)
- `GET /api/image-gallery/{id}` - Read image metadata
- `GET /api/image-gallery/{id}/data` - Download raw binary image data
- `GET /api/image-gallery/url/data?url=<url>` - Fetch image data by URL
- `PUT /api/image-gallery/{id}` - Update image name/details
- `DELETE /api/image-gallery/{id}` - Delete image

### 5.8 Comments (`/api/comments`)
- `GET /api/comments` - List comments
- `POST /api/comments` - Add a comment (Parameters: `page_id`, `text` or `html`, `parent_id` for replies)
- `GET /api/comments/{id}` - Read comment
- `PUT /api/comments/{id}` - Update comment
- `DELETE /api/comments/{id}` - Delete comment

### 5.9 Content Permissions (`/api/content-permissions/{contentType}/{contentId}`)
- `GET /api/content-permissions/{contentType}/{contentId}` - Read custom permissions on `bookshelf`, `book`, `chapter`, or `page`
- `PUT /api/content-permissions/{contentType}/{contentId}` - Set explicit role permissions (view, create, update, delete) or restrict inheritance

### 5.10 Users & Roles
- `GET /api/users` / `POST /api/users` / `GET /api/users/{id}` / `PUT /api/users/{id}` / `DELETE /api/users/{id}`
- `GET /api/roles` / `POST /api/roles` / `GET /api/roles/{id}` / `PUT /api/roles/{id}` / `DELETE /api/roles/{id}`

### 5.11 Audit Log & Recycle Bin
- `GET /api/audit-log` - Query activity log with date and user filters
- `GET /api/recycle-bin` - List soft-deleted items
- `PUT /api/recycle-bin/{deletionId}` - Restore a deleted item
- `DELETE /api/recycle-bin/{deletionId}` - Permanently purge a deleted item

### 5.12 Tags & System Info
- `GET /api/tags/names` - List all unique tag names in use
- `GET /api/tags/values-for-name?name=<tag_name>` - List all values associated with a tag name
- `GET /api/system` - System metadata and version info
- `GET /api/docs.json` - Raw OpenAPI 3.0 schema

---

## 6. Code Examples & Reusable Snippets

### cURL

#### List Books (Filtered & Sorted)
```bash
curl -s --request GET \
  --url "https://wiki.example.com/api/books?count=10&sort=-updated_at&filter[name:like]=%Guide%" \
  --header "Authorization: Token YOUR_TOKEN_ID:YOUR_TOKEN_SECRET"
```

#### Create a New Markdown Page
```bash
curl -s --request POST \
  --url "https://wiki.example.com/api/pages" \
  --header "Authorization: Token YOUR_TOKEN_ID:YOUR_TOKEN_SECRET" \
  --header "Content-Type: application/json" \
  --data '{
    "book_id": 5,
    "name": "Getting Started with the API",
    "markdown": "# Welcome\nThis page was created automatically via the BookStack REST API.",
    "tags": [
      {"name": "Category", "value": "API"},
      {"name": "Status", "value": "Draft"}
    ]
  }'
```

#### Upload an Attachment File
```bash
curl -s --request POST \
  --url "https://wiki.example.com/api/attachments" \
  --header "Authorization: Token YOUR_TOKEN_ID:YOUR_TOKEN_SECRET" \
  --form "uploaded_to=12" \
  --form "name=ArchitectureDiagram" \
  --form "file=@/path/to/diagram.pdf"
```

---

### Python Helper Class

```python
import requests
from typing import Any, Dict, Optional

class BookStackClient:
    def __init__(self, base_url: str, token_id: str, token_secret: str):
        self.base_url = base_url.rstrip("/") + "/api"
        self.headers = {
            "Authorization": f"Token {token_id}:{token_secret}",
            "Content-Type": "application/json"
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()

    def get_books(self, count: int = 20, offset: int = 0, sort: str = "+name") -> Dict[str, Any]:
        params = {"count": count, "offset": offset, "sort": sort}
        return self._request("GET", "books", params=params)

    def create_page(self, book_id: int, name: str, markdown: str, tags: Optional[list] = None) -> Dict[str, Any]:
        payload = {
            "book_id": book_id,
            "name": name,
            "markdown": markdown,
            "tags": tags or []
        }
        return self._request("POST", "pages", json=payload)

    def search(self, query: str, page: int = 1, count: int = 20) -> Dict[str, Any]:
        params = {"query": query, "page": page, "count": count}
        return self._request("GET", "search", params=params)

    def export_page_markdown(self, page_id: int) -> str:
        url = f"{self.base_url}/pages/{page_id}/export/markdown"
        res = requests.get(url, headers={"Authorization": self.headers["Authorization"]})
        res.raise_for_status()
        return res.text

# Usage Example:
if __name__ == "__main__":
    client = BookStackClient(
        base_url="https://wiki.example.com",
        token_id="YOUR_TOKEN_ID",
        token_secret="YOUR_TOKEN_SECRET"
    )
    # Search for pages tagged 'API'
    results = client.search("[Category=API]")
    print(results)
```

---

### Node.js / JavaScript Example

```javascript
import axios from 'axios';

class BookStackAPI {
  constructor(baseUrl, tokenId, tokenSecret) {
    this.client = axios.create({
      baseURL: `${baseUrl.replace(/\/$/, '')}/api`,
      headers: {
        Authorization: `Token ${tokenId}:${tokenSecret}`,
        'Content-Type': 'application/json',
      },
    });
  }

  async listPages(options = {}) {
    const response = await this.client.get('/pages', { params: options });
    return response.data;
  }

  async createPage({ bookId, chapterId, name, markdown, html, tags = [] }) {
    const payload = { name, tags };
    if (bookId) payload.book_id = bookId;
    if (chapterId) payload.chapter_id = chapterId;
    if (markdown) payload.markdown = markdown;
    if (html) payload.html = html;

    const response = await this.client.post('/pages', payload);
    return response.data;
  }

  async search(query) {
    const response = await this.client.get('/search', { params: { query } });
    return response.data;
  }
}

// Example usage:
const bookstack = new BookStackAPI(
  'https://wiki.example.com',
  'YOUR_TOKEN_ID',
  'YOUR_TOKEN_SECRET'
);

bookstack.listPages({ count: 5, sort: '-created_at' })
  .then(res => console.log('Pages:', res))
  .catch(err => console.error('Error:', err.response?.data || err.message));
```

---

## 7. Official Resources & References
- **Official API Documentation Endpoint**: `/api/docs`
- **BookStack API Scripts (Codeberg)**: [https://codeberg.org/bookstack/api-scripts](https://codeberg.org/bookstack/api-scripts)
- **BookStack Search Documentation**: [https://www.bookstackapp.com/docs/user/searching/](https://www.bookstackapp.com/docs/user/searching/)
- **BookStack Main Repository**: [https://github.com/BookStackApp/BookStack](https://github.com/BookStackApp/BookStack)
