# Flashcards Generator TUI Design System

## 1. Atmosphere & Identity

A compact, dependable terminal workspace. The signature is a single bordered workflow surface with muted supporting copy and accent-colored hierarchy, optimized for keyboard use rather than decoration.

## 2. Color

The TUI uses Textual semantic theme tokens exclusively.

| Role | Token | Usage |
|---|---|---|
| App surface | `$surface` | Screen, status wells, nested panels |
| Raised surface | `$panel` | Header and workflow panel |
| Primary text | `$text` | Body and control content |
| Supporting text | `$text-muted` | Labels and inline guidance |
| Accent | `$accent` | Titles and section emphasis |
| Border | `$primary-darken-2` | Non-destructive panel outlines |
| Destructive | `$error` | Destructive actions and confirmations |

No raw colors are introduced in TUI CSS.

## 3. Typography

Terminal cell metrics and the user's terminal font own type size and family. Hierarchy uses Textual `text-style: bold` for workflow and section titles; body, labels, and hints remain regular weight.

## 4. Spacing & Layout

Textual cell units form the spacing scale: `1` cell for related controls and `2` cells for section padding. The screen shell fixes Header, tab navigation, and Footer; each `TabPane` owns vertical scrolling. Workflow panels use the available width and never require horizontal scrolling at 52 columns.

## 5. Components

### Workflow Panel
- **Structure:** title, controls, status.
- **Spacing:** 2-cell vertical and 3-cell horizontal padding; related rows use 1 cell.
- **States:** normal, focused child, validation error, saved/success.
- **Accessibility:** source-order keyboard navigation; visible Textual focus states.
- **Layout:** vertical stack inside the scrolling `TabPane`.

### Field Block
- **Structure:** label, control, optional persistent hint.
- **Variants:** text input, closed-set select, checkbox.
- **Spacing:** 1 cell before each label; hint sits directly below its control.
- **States:** empty/placeholder, focused, selected, invalid through the panel status.
- **Accessibility:** labels precede controls; guidance remains visible without hover.

### Action Row
- **Structure:** related buttons and checkboxes.
- **Spacing:** one cell above; controls share available width.
- **States:** Textual default, hover, focus, active, disabled.
- **Accessibility:** keyboard reachable; global shell shortcuts retain priority.

## 6. Motion & Interaction

Use Textual's built-in focus, press, select, and tab transitions only. No decorative motion. Shell shortcuts remain available while form controls are focused.

## 7. Depth & Surface

Borders plus tonal shift: `$panel` separates the workflow from `$surface`, and `round $primary-darken-2` outlines functional containers. No shadows or decorative layers.

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- Full keyboard operation at 52x24 and larger.
- Persistent guidance must not depend on mouse hover.
- Closed values use selection controls; open provider codes remain editable.
- No horizontal overflow; the tab pane is the only settings scroll owner.

### Accepted Debt

None for the Settings guidance work.
