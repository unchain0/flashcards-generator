# Flashcards Generator Web Design System

## Product Surface

The application is operated in the browser. The design centers on the real
task: upload source material, configure generation, follow its job, and
download CSV artifacts. NotebookLM authentication and generation run in the
local companion on the user's computer; the interface enables uploads only
after that local profile is authenticated.

## Web Generator Design System

The Litestar dashboard is an operate-mode generation workspace. Its visual
voice is deep blue night surfaces with a single acid-green action accent,
using tonal separation instead of stacked card shadows. The type stack favors
a humanist sans voice over the generic Inter default. The page is organized
around the real task: upload source material, configure the generation, follow
the local job, and download its CSV artifacts.

The browser sends documents directly to the Flashcards Companion running on
the user's computer. The companion uses that user's local NotebookLM session,
keeps inputs in a private temporary workspace, and returns generated CSV files
to the browser without sending source documents to the web server.

Design Read: a browser workbench for learners turning source documents into
their own review workflow, with ENERGY 2 / RHYTHM 2 / MOTION 1. The night-blue
surfaces give the generator a distinct identity, the acid-green accent marks
the primary action, the humanist sans keeps instructions easy to scan, and the
content order follows the actual generation flow. Borders and tonal surfaces
separate working areas without decorative elevation; motion stays on direct
control feedback.

### Tokens

| Role | Token | Value |
| --- | --- | --- |
| Canvas | `--canvas` | `#08111f` |
| Panel | `--surface` | `#102238` |
| Raised surface | `--surface-raised` | `#162d45` |
| Input surface | `--surface-input` | `#0d1b2b` |
| Primary text | `--text` | `#edf4fb` |
| Supporting text | `--supporting` | `#c8d7e5` |
| Accent | `--accent` | `#9be15d` |
| Accent hover | `--accent-hover` | `#b4ed7d` |
| Accent ink | `--accent-ink` | `#102016` |
| Error state | `--error` | `#ff9888` |
| Panel border | `--border` | `#5a7d99` |
| Input border | `--border-input` | `#5a7d99` |
| Body type | `--font-body` | `Avenir Next`, `Trebuchet MS`, `Segoe UI`, sans-serif |
| Type scale | `--font-*` | Body `1rem`, label `0.9rem`, lede `1.125rem`, section `1.5rem`, display `2rem–4rem` |
| Spacing scale | `--space-1`–`--space-7` | `0.25rem`, `0.5rem`, `0.75rem`, `1rem`, `1.5rem`, `2rem`, `2.5rem` |
| Shell spacing | `--space-shell-top`, `--space-5`, `--space-shell-bottom` | `4.5rem 1.5rem 5rem` |
| Compact shell | `--space-shell-top-compact` | `2.8rem` |
| Content measure | `--content-max`, `--hero-copy-max`, `--heading-measure`, `--lede-measure` | `70rem`, `45rem`, `20ch`, `65ch` |
| Component measures | `--auth-panel-max`, `--workspace-status-min`, `--status-panel-min-height`, `--control-min-height` | `28.75rem`, `18.75rem`, `17.5rem`, `2.75rem` |
| Focus and borders | `--border-width`, `--focus-ring-width`, `--focus-ring-offset` | `1px`, `3px`, `3px` |
| Control radius | `--radius-control` | `10px` |
| Panel radius | `--radius-panel` | `16px` |
| Responsive breakpoints | media-query values | `820px`, `620px`, `360px`; kept literal because CSS custom properties cannot be used reliably in media queries |

### Components and states

- Panels use one border and a tonal background; no stacked shadow treatment.
- Controls expose hover, pressed, disabled, and `:focus-visible` states.
- The heading carries the page hierarchy directly; standalone eyebrow/kicker
  labels are not used above headings.
- Empty, loading, error, running, completed, and download states are visible
  in the status panel.
- The layout collapses to one column below `820px` without horizontal scroll.
- Reduced-motion users receive the same states without transitions.
