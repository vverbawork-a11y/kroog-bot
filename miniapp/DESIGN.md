# Kroog Mini App: layout rules

The workshop photographs, supplied white-violet artwork, burgundy text, cream
background and Monument typeface are the existing identity. Keep them. Prefer
plain copy and real product photography over extra panels, icons or decorative
badges. Do not turn editorial paragraphs into individual cards.

## Geometry

Source of truth: `assets/brand.css` (keep `brand.css` in sync).

- `--kroog-radius`: 12px for cards, images, figures, inputs, buttons and messages.
- `--kroog-gutter`: 20px at both page edges, with device safe areas respected.
- Spacing: 4, 8, 12, 16, 24, 32 and 48px via `--kroog-space-*` tokens.
- Content starts 24px below the header on every screen. Page headings have no
  top margin and 24px below them. Paragraphs and image-to-copy gaps use 24px.
- Section subheadings: 32px above, 16px below. Product and option grids: 12px gaps.
- Card captions: 12px padding. Form fields and controls use the same spacing scale.

Keep screen wrappers unpadded: `main` owns the page gutter. Avoid additional
horizontal offsets for About, Works or checkout. Each component should have one
set of styles instead of another override at the end of the stylesheet.

## Images added later

Content images inside `main` receive the shared radius automatically. Use a
`figure` for a clipped photo and caption group; it has the same radius and a
24px following gap. Product photos stay in `.tile-media`; retain their existing
square dimensions, generated previews and uncropped disc geometry. Logos and
full-screen ambient artwork are outside this content-image rule.

Reference principles: https://vibemole.com/resources/avoid-vibecoded-app-design
Use clear hierarchy, actual product evidence and fewer nested wrappers.
