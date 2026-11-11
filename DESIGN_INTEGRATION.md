# REUSE design integration (design.zip → existing system)

The REUSE design (maroon brand, white sidebar with grouped navigation, top bar with breadcrumb,
stat cards, panels, tables, status pills, cards, forms, modals and toasts) now drives every page.
Only the frontend changed: no models, migrations, views, URLs, API endpoints or business rules were touched.

## Frontend files

| File | Change |
| --- | --- |
| `static/css/style.css` | Rewritten as the single design-system stylesheet (tokens, shell, components, auth, responsive rules from the design). |
| `static/css/admin.css` | Removed; its rules now live in `style.css`. |
| `static/img/reuse-logo-maroon.png` | New: logo from design.zip. |
| `static/img/placeholder.svg`, `static/img/avatar.svg` | Restyled to the design's resource-visual / avatar look. |
| `static/js/shell.js` | New: sidebar collapse («/», remembered per browser) and "open form" links. |
| `static/js/charts.js` | New: draws the admin dashboard line chart and donut from real values in the page. |
| `static/js/common.js` | Top-bar bell dot + sidebar unread badge; every Sign Out button uses the existing logout flow. |
| `static/js/dashboard.js`, `browse.js`, `requests.js`, `borrowed.js`, `notifications.js` | Same API calls and logic; markup now uses the design's rows, resource cards, tables and buttons. `borrowed.js` keeps the sidebar, heading and URL in step with the Active / History tab. |
| `templates/base_app.html`, `templates/admin/base_admin.html`, `templates/base_auth.html` | New shells (sidebar, top bar, page heading). |
| `templates/partials/icon.html`, `templates/partials/avatar.html` | New shared partials (design icon set, initials/photo avatar). |
| All page templates under `templates/` and `templates/admin/` | Restyled; every form field name, element id, URL, CSRF token and action is unchanged. |

## Page mapping

| Existing page | Design source |
| --- | --- |
| User dashboard | Dashboard (user): stats, panels, activity rows |
| Browse items | Browse Resources: toolbar + resource cards |
| My requests | My Requests: toolbar + table |
| Borrowed items (Active / History) | My Borrowings and Borrowing History (sidebar entries + tables) |
| Notifications | Notifications panel |
| Profile | My Profile form card |
| Login, sign-up, reset / change password | Not in the design; adapted using the form card, fields and buttons |
| Admin dashboard | System Overview: stats, Borrowing Activity chart, Request Outcomes donut, Recommended Actions, activity rows (all real data) |
| Manage items | Resource Management + Add New Resource form |
| Borrowing requests | Borrow Request Management |
| Active borrowings, Return management | Returns & Records |
| User management, add user, user detail | User Management (detail page adapted with panels) |
| Admin notifications | Not in the design; adapted as a table page |

Design screens with no backend yet (Analytics & Reports, Reports, System Settings) were not added.
The Django built-in admin (`/admin/`) keeps Django's own look.
