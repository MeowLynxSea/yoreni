# Brand artwork

`dock-icon.png` is the 1024-pixel app icon: a chibi anime character with a teal bob and a coral hairpin on a navy rounded square, with softened edges and a flat, minimal palette.

The master illustration lives at `webui/brand-source/yoreni_icon.png`. The WebUI exports in `webui/public/brand/` (PWA icons, apple touch icon, favicon, maskable icon, mark and wordmark SVGs) and this dock icon are all derived from it, so regenerate every size from that file rather than editing an individual export.

The packaged helper loads this PNG through AppKit. It needs neither a generated application executable nor an icon compiler at installation or launch. Keep artwork changes independent of gateway lifecycle policy.
