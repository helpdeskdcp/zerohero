# Third-party software shipped with ZeroHero's frontend

## TradingView Lightweight Charts 5.2.1
- Source: https://registry.npmjs.org/lightweight-charts/-/lightweight-charts-5.2.1.tgz (npm, published 2026-08-12)
- Upstream: https://github.com/tradingview/lightweight-charts (GitHub licence detection: Apache-2.0)
- File vendored unmodified: `vendor/lightweight-charts/lightweight-charts.standalone.production.js`
  - sha256 `e21cc5caa0226ef30bd8549c50b9ef926615f2a4ee6b4e486353477a55f598cf`
  - npm `dist.integrity` (sha512) verified against the downloaded tarball before extraction
  - inspected before use: no `eval`, `new Function`, `fetch`, `XMLHttpRequest`, `WebSocket`, `sendBeacon` or storage access;
    the only URLs in the file are the Apache licence URL, a https://www.tradingview.com/ attribution link and the SVG namespace
- Licence: Apache License 2.0 (`vendor/lightweight-charts/LICENSE`); attribution in `vendor/lightweight-charts/NOTICE`.
  Pages that render the chart keep the library's TradingView attribution link visible (do not hide it with `attributionLogo: false`).
- Dependency note: the standalone build bundles `fancy-canvas` 2.1.0 (declared npm dependency); no separate install is required.
- Changes made by ZeroHero: none to the vendored file. To upgrade: repeat the download, integrity check and inspection above, then update this file.
