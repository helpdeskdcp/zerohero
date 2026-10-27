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

## Smart Chart bundle (Vela, Vela-PineTS, PineTS and their dependencies)
- Files: `vendor/smartchart/smartchart-vendor.js` (built, not hand-vendored) plus licence material in the same directory.
- Source: npm packages pinned in `backend/app/smart_chart/package.json` / `package-lock.json` (integrity hashes recorded in `vendor/smartchart/THIRD_PARTY_NOTICES.md`);
  built by `backend/app/smart_chart/web/build.mjs` (esbuild). Restore with `npm ci`, rebuild with `npm run build:web`.
- Licences: `@luxalgo/vela` Apache-2.0 (its NOTICE requires a visible attribution; the built-in watermark is left on and the page footer links Vela);
  `@luxalgo/vela-pinets` and `pinets` AGPL-3.0-only; the remaining packages are MIT. Full texts: `vendor/smartchart/THIRD_PARTY_LICENSES.txt`,
  `LICENSE-*.txt`, `NOTICE-vela.txt`. How AGPL-3.0 applies to this deployment is an open owner decision: see `backend/SMART_CHART.md` ("Licensing").
- Changes made by ZeroHero to the upstream packages: none. To upgrade: bump the pins, `npm ci`, `npm run build:web`, review the regenerated notices, run the Smart Chart tests.
