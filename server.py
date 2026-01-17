import argparse
import datetime as dt
import html
import mimetypes
import pathlib
import shutil
import socketserver
import sys
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler


DEFAULT_ROOT = r"D:\\"
MAX_PREVIEW_BYTES = 1_000_000


def format_size(size):
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def file_mtime(stat):
    return dt.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")


def is_text_type(mime_type, suffix):
    if mime_type and mime_type.startswith("text/"):
        return True
    if mime_type in {"application/json", "application/xml"}:
        return True
    return suffix in {".txt", ".md", ".py", ".log", ".csv", ".ini", ".cfg", ".yaml", ".yml"}


class ThreadingHTTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def make_handler(root_path, max_preview_bytes):
    root_path = root_path.resolve()

    class FileServerHandler(BaseHTTPRequestHandler):
        server_version = "DFileServer/1.0"

        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path in ("/", "/browse"):
                self.handle_browse(parsed)
                return
            if parsed.path == "/preview":
                self.handle_preview(parsed)
                return
            if parsed.path == "/download":
                self.handle_download(parsed)
                return
            if parsed.path == "/raw":
                self.handle_raw(parsed)
                return
            self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

        def handle_browse(self, parsed):
            rel = self.get_rel_path(parsed)
            target = self.safe_path(rel)
            if not target or not target.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Path not found")
                return
            if target.is_file():
                self.redirect(f"/preview?path={urllib.parse.quote(rel)}")
                return

            rows = []
            try:
                entries = list(target.iterdir())
            except OSError:
                self.send_error(HTTPStatus.FORBIDDEN, "Access denied")
                return

            entries.sort(key=lambda p: (not p.is_dir(), p.name.lower()))
            for entry in entries:
                rel_entry = self.rel_from_root(entry)
                stat = entry.stat()
                name = html.escape(entry.name)
                size = "-" if entry.is_dir() else format_size(stat.st_size)
                mtime = file_mtime(stat)
                link = f"/browse?path={urllib.parse.quote(rel_entry)}" if entry.is_dir() else f"/preview?path={urllib.parse.quote(rel_entry)}"
                download = "" if entry.is_dir() else f'<a class="action" href="/download?path={urllib.parse.quote(rel_entry)}">download</a>'
                rows.append(
                    "<tr>"
                    f'<td><a class="name" href="{link}">{name}</a></td>'
                    f"<td>{size}</td>"
                    f"<td>{mtime}</td>"
                    f"<td>{download}</td>"
                    "</tr>"
                )

            breadcrumb = self.render_breadcrumb(rel)
            body = f"""
<div class="header">
  <h1>Drive D file server</h1>
  <div class="crumbs">{breadcrumb}</div>
</div>
<table>
  <thead>
    <tr><th>Name</th><th>Size</th><th>Modified</th><th>Action</th></tr>
  </thead>
  <tbody>
    {''.join(rows) if rows else '<tr><td colspan="4">Empty folder</td></tr>'}
  </tbody>
</table>
"""
            self.write_html("Browse", body)

        def handle_preview(self, parsed):
            rel = self.get_rel_path(parsed)
            target = self.safe_path(rel)
            if not target or not target.exists():
                self.send_error(HTTPStatus.NOT_FOUND, "Path not found")
                return
            if target.is_dir():
                self.redirect(f"/browse?path={urllib.parse.quote(rel)}")
                return

            stat = target.stat()
            mime_type, _ = mimetypes.guess_type(str(target))
            suffix = target.suffix.lower()
            rel_quoted = urllib.parse.quote(rel)

            details = f"<div class='meta'>Size: {format_size(stat.st_size)} | Modified: {file_mtime(stat)}</div>"
            back = f'<div class="back"><a href="/browse?path={urllib.parse.quote(self.parent_rel(rel))}">Back</a></div>'

            if stat.st_size > max_preview_bytes and not is_text_type(mime_type, suffix):
                body = f"""
<h1>{html.escape(target.name)}</h1>
{details}
<p>Preview disabled for large binary files.</p>
<p><a class="action" href="/download?path={rel_quoted}">Download</a></p>
{back}
"""
                self.write_html("Preview", body)
                return

            preview_html = self.build_preview_html(target, rel, mime_type, suffix, max_preview_bytes)
            body = f"""
<h1>{html.escape(target.name)}</h1>
{details}
{preview_html}
<div class="actions">
  <a class="action" href="/download?path={rel_quoted}">Download</a>
  <a class="action" href="/raw?path={rel_quoted}">Open raw</a>
</div>
{back}
"""
            self.write_html("Preview", body)

        def handle_download(self, parsed):
            rel = self.get_rel_path(parsed)
            target = self.safe_path(rel)
            if not target or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            self.send_file(target, as_attachment=True)

        def handle_raw(self, parsed):
            rel = self.get_rel_path(parsed)
            target = self.safe_path(rel)
            if not target or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            self.send_file(target, as_attachment=False)

        def build_preview_html(self, target, rel, mime_type, suffix, max_bytes):
            rel_quoted = urllib.parse.quote(rel)
            if is_text_type(mime_type, suffix):
                if target.stat().st_size > max_bytes:
                    return "<p>File is too large to preview as text.</p>"
                try:
                    content = target.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    return "<p>Unable to read file.</p>"
                escaped = html.escape(content)
                return f"<pre>{escaped}</pre>"

            if mime_type and mime_type.startswith("image/"):
                return f'<div class="media"><img src="/raw?path={rel_quoted}" alt="image preview"></div>'
            if mime_type and mime_type.startswith("audio/"):
                return f'<div class="media"><audio controls src="/raw?path={rel_quoted}"></audio></div>'
            if mime_type and mime_type.startswith("video/"):
                return f'<div class="media"><video controls src="/raw?path={rel_quoted}"></video></div>'
            if mime_type == "application/pdf":
                return f'<div class="media"><iframe src="/raw?path={rel_quoted}" title="pdf preview"></iframe></div>'

            return "<p>No inline preview available for this file type.</p>"

        def render_breadcrumb(self, rel):
            if not rel:
                return "D:\\"
            parts = [p for p in rel.replace("\\", "/").split("/") if p]
            crumbs = ['<a href="/browse">D:\\</a>']
            current = ""
            for part in parts:
                current = f"{current}/{part}" if current else part
                crumbs.append(f'<a href="/browse?path={urllib.parse.quote(current)}">{html.escape(part)}</a>')
            return " / ".join(crumbs)

        def parent_rel(self, rel):
            if not rel:
                return ""
            parts = [p for p in rel.replace("\\", "/").split("/") if p]
            return "/".join(parts[:-1])

        def rel_from_root(self, target):
            return str(target.relative_to(root_path)).replace("\\", "/")

        def get_rel_path(self, parsed):
            params = urllib.parse.parse_qs(parsed.query)
            rel = params.get("path", [""])[0]
            return rel.replace("\\", "/")

        def safe_path(self, rel):
            rel = rel.strip().replace("\\", "/")
            if not rel:
                return root_path
            rel_path = pathlib.Path(rel)
            if rel_path.is_absolute() or rel_path.drive or ":" in rel:
                return None
            candidate = (root_path / rel_path).resolve(strict=False)
            try:
                candidate.relative_to(root_path)
            except ValueError:
                return None
            return candidate

        def send_file(self, path, as_attachment):
            mime_type, _ = mimetypes.guess_type(str(path))
            content_type = mime_type or "application/octet-stream"
            size = path.stat().st_size
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(size))
            disposition = "attachment" if as_attachment else "inline"
            filename = path.name.replace('"', "")
            quoted = urllib.parse.quote(filename)
            self.send_header(
                "Content-Disposition",
                f"{disposition}; filename=\"{filename}\"; filename*=UTF-8''{quoted}",
            )
            self.end_headers()
            with path.open("rb") as fh:
                shutil.copyfileobj(fh, self.wfile)

        def write_html(self, title, body):
            html_doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      color-scheme: light;
    }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
      background: #f4f6f8;
      color: #1f2328;
    }}
    .header {{
      padding: 24px 32px 8px;
      background: linear-gradient(135deg, #eaf2ff, #fdfdfd);
      border-bottom: 1px solid #e1e4e8;
    }}
    h1 {{
      margin: 0 0 8px;
      font-size: 22px;
    }}
    .crumbs {{
      font-size: 14px;
    }}
    .crumbs a {{
      color: #0a58ca;
      text-decoration: none;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: #ffffff;
    }}
    thead {{
      background: #f8f9fb;
    }}
    th, td {{
      padding: 12px 16px;
      border-bottom: 1px solid #edf0f3;
      text-align: left;
      font-size: 14px;
    }}
    .name {{
      color: #0a58ca;
      text-decoration: none;
    }}
    .action {{
      color: #0a58ca;
      text-decoration: none;
      font-weight: 600;
      margin-right: 8px;
    }}
    .meta {{
      padding: 0 32px 16px;
      font-size: 13px;
      color: #5a6270;
    }}
    pre {{
      background: #0d1117;
      color: #e6edf3;
      padding: 16px;
      border-radius: 10px;
      overflow: auto;
      margin: 0 32px 16px;
      max-height: 70vh;
    }}
    .media {{
      padding: 0 32px 16px;
    }}
    img, video, iframe {{
      max-width: 100%;
      border-radius: 12px;
      border: 1px solid #e1e4e8;
      background: #ffffff;
    }}
    iframe {{
      width: 100%;
      height: 70vh;
    }}
    .actions {{
      padding: 0 32px 16px;
    }}
    .back {{
      padding: 0 32px 32px;
    }}
  </style>
</head>
<body>
  {body}
</body>
</html>
"""
            data = html_doc.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, target):
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", target)
            self.end_headers()

        def log_message(self, fmt, *args):
            sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), fmt % args))

    return FileServerHandler


def main():
    parser = argparse.ArgumentParser(description="Simple LAN file server for Drive D")
    parser.add_argument("--root", default=DEFAULT_ROOT, help="Root folder to serve (default: D:\\)")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind (default: 8000)")
    parser.add_argument("--max-preview-bytes", type=int, default=MAX_PREVIEW_BYTES, help="Max bytes for text preview")
    args = parser.parse_args()

    root = pathlib.Path(args.root)
    if not root.exists() or not root.is_dir():
        print(f"Root path not found: {root}")
        sys.exit(1)

    mimetypes.init()
    handler = make_handler(root, args.max_preview_bytes)
    with ThreadingHTTPServer((args.host, args.port), handler) as httpd:
        print(f"Serving {root} at http://{args.host}:{args.port}")
        httpd.serve_forever()


if __name__ == "__main__":
    main()
