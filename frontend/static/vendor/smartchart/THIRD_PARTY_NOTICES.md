# Smart Chart vendor bundle

`smartchart-vendor.js` is built by `backend/app/smart_chart/web/build.mjs` (esbuild 0.28.2, versions pinned in `package.json` / `package-lock.json`;
restore with `npm ci`, rebuild with `npm run build:web`). The package list below is generated: packages esbuild bundled as separate inputs, plus the declared runtime dependencies of those packages that upstream's prebuilt files may inline (marked "inlined by upstream dist").

- Bundle sha256: `59c95e918df3238229d743a6f3f73ef2200800eb37852267aa1b3dad2f68e99a`
- Full licence texts of every bundled package: `THIRD_PARTY_LICENSES.txt` (same directory)
- Apache-2.0 text and Vela's NOTICE: `LICENSE-vela-Apache-2.0.txt`, `NOTICE-vela.txt` — the Vela attribution mark stays visible on every Smart Chart page (built-in watermark left enabled, plus a text link in the page footer).
- AGPL-3.0 texts: `LICENSE-pinets-AGPL-3.0.txt`, `LICENSE-vela-pinets-AGPL-3.0.txt`

## Packages (29)

| Package | Version | Licence | npm integrity (package-lock.json) |
| --- | --- | --- | --- |
| @floating-ui/core | 1.8.0 | MIT | `sha512-0CIZ5itps/8x7BG8dEIhs53BvCUH2PCoogtakwRTut+Arm58sJooJ0AuZhLw2HJYIR5cMLNPBSS728sPho2khQ==` |
| @floating-ui/dom | 1.8.0 | MIT | `sha512-yXSrzeHZBTZadLOlfyhCkJHNeLJnHRnRInwdZ40L7ZiaAtrBwoYlsDrX3v5zB1Utk7CLfzcOVnVVWoXEky7Ceg==` |
| @floating-ui/utils | 0.2.12 | MIT | `sha512-HpCo8tmWzLVad5s2d19EhAz5zqrrQ6s69qd6moPMQvkOuSwDT1YgRfWSVuc4ennqrgv3OHppiOGMQ7oC13yIww==` |
| @luxalgo/vela | 0.9.0 | Apache-2.0 | `sha512-OOGEPhW+m4pOw2+Jg0uxGUPZA7Gt4Ih/L6r6eUQ24hJww4Jhhan3Feiavs33EKoKZK19zQi03SrYgmklzW5DBg==` |
| @luxalgo/vela-pinets | 0.3.0 | AGPL-3.0-only | `sha512-KoZFbKlZPaTmIk9J9zMNrx+bTF3IwLdQhOm38A1TPofXJR9X4C1mPQZASVu5mZztAXp5Dw4hAACBLv9RD9CInQ==` |
| @zag-js/anatomy | 1.46.0 | MIT | `sha512-geWPKs9FtZ3FihwOyW/swG3IlJxqGydiAEUHRjBq3q45IarxV4gJZfpvWGlv20mVjOhbWkNEG+CDPsUGw4ixow==` |
| @zag-js/aria-hidden | 1.46.0 | MIT | `sha512-TQRJxzT4wzXHMKX+mnnm4qWEU6ASpkYcpR6/h3oaJ/3g2AreUYzmThdsS1FwDl0SkdoQEsuCQXWrMpHXUdAmJg==` |
| @zag-js/core | 1.46.0 | MIT | `sha512-zwiTDz9iBi1a/CyzdaeI8TlBADmeLCDNONh930a7x32TpduN8LiY26X58zrgimvCowOu7YIFNzI/vqW7Wp1phA==` |
| @zag-js/dialog | 1.46.0 | MIT | `sha512-OCJl9WWOjoPRaX6mhIRGxZ0b28BOm3bRokMTuf+wDQ6uowgNGZRhRW3TIFRtGJBmpEEVOF7p92b4qpG6Q1YSDg==` |
| @zag-js/dismissable | 1.46.0 | MIT | `sha512-HramN58RwrpuSEdrN7XecL7g5wvwck3HJE81s7zli2qP/kIaVLodE5qbzR/PW4715daM1rO+Ptsd96dkeBzICQ==` |
| @zag-js/dom-query | 1.46.0 | MIT | `sha512-ibkO0VkQ2rLaclrO/O7kunvvwK8ZiW2L78arxsPYOEWmTqdNM6eQh721l9jGREUggh8nGsydNaoJH7lP3z1DUg==` |
| @zag-js/focus-trap | 1.46.0 | MIT | `sha512-c+CiVRGj4yGQeAwqJB5GGo0kERrpR5MxjohpKcZuIy7uaQSXsxY+TzSjP2+JRBSDHMMV2UuCdkV+G0RbWyzEjQ==` |
| @zag-js/focus-visible | 1.46.0 | MIT | `sha512-H/npFbU9yRLWhzPud/uuGKxwtsh/FcMaL5IisBJxo3txqotbfcgPNN9VvqQY2BuSbJi2gg01u9ngwBIZPVaUmQ==` |
| @zag-js/interact-outside | 1.46.0 | MIT | `sha512-gzOj8zNvZzrF8l7ikYcYmdOlhLXEBGm7Qsp++hE/in47Nhi1uHWlamW21orqYdNF8Y250qrK24vIr0ZW4xTPcw==` |
| @zag-js/menu | 1.46.0 | MIT | `sha512-oQZ0SUI4AWQP5CocSVyiP55SVQZUa6eHTl3Ia8W0ByH/LK6ShFHTpEr/7eDt6uoHT9pDMpAscIIoMF70LvunTg==` |
| @zag-js/popper | 1.46.0 | MIT | `sha512-tpTOiwWY8FRQHzKycKbj+aJbhMpjvTsH871XXQdB2H+56eJ7sgffECCJZTByq3qsC+d2g/WCJqAfLJqgtYagww==` |
| @zag-js/rect-utils | 1.46.0 | MIT | `sha512-w9USQ+fm2Ad9Z9loSdGic5pawCP2ALHW1p1IES3J6neXcbd4PyAX744qmUq3AckdVK+c88PSRL02i1ctCOFmYQ==` |
| @zag-js/remove-scroll | 1.46.0 | MIT | `sha512-i/GbBWtlmWBpA3YGCWxIBnzNhEtZ42o7rjRL9JrHb7xX6oyHzDB5sVT9oImrB3ih63V5/Y5YTbkkAAmL5D9G0w==` |
| @zag-js/store | 1.46.0 | MIT | `sha512-NXjE6pITQaORVDxSX5nriNMtIBJGpq92ni3YvcZ1f37CZO2qXumOnYzyWWJNSwDMpfqEmPh83cW0Nxse7AUILA==` |
| @zag-js/tooltip | 1.46.0 | MIT | `sha512-H0Ov0bDW8thYqRO/VI5RW4vVc/qOW8S8du9uI5mSisyyY25GiG5N2iuOiM0kaYXBo1wUTvMJFsBdfMwhi4z9oA==` |
| @zag-js/types | 1.46.0 | MIT | `sha512-HwIvuXCGx/wgCNNXYvKyCJ62e/QmIi02v9GsA0icFxDKxv6i7J2mZmAQ/vF5Nmh3q80Nj5CZ1DdQ7bXYt/Yzww==` |
| @zag-js/utils | 1.46.0 | MIT | `sha512-SOKa832hWCoXaYB2Pcdb0c+ZcyNSuAAPpL5YRnVHskuc0BEsHPWOhxSIxuGF1gvz4RsfBKiAHrDoFDl8JLSOEg==` |
| @zag-js/vanilla | 1.46.0 | MIT | `sha512-ykPAY7xIrUmTwumaoETbHaTjVLY7R8gafQvwQfESi7xKpQmXpImKXdBGttIY9Hl3hAD1k8kXjZdaB+HkP1ciVg==` |
| acorn (inlined by upstream dist) | 8.19.0 | MIT | `sha512-oJlA3XiRm7Cyk6qFD2Jn8ak9B7jSy0qy00ADO3+8dpT0LSjFihQYv4C02LFCSYJV3Q37xYwwRy5m+IpIiUzqWw==` |
| acorn-walk (inlined by upstream dist) | 8.3.5 | MIT | `sha512-HEHNfbars9v4pgpW6SO1KSPkfoS0xVOM/9UzkJltjlsHZmJasxg8aXkuZa7SMf8vKGIBhpUsPluQSqhJFCqebw==` |
| astring (inlined by upstream dist) | 1.9.0 | MIT | `sha512-LElXdjswlqjWrPpJFg1Fx4wpkOCxj1TDHlSV4PlaRxHGWko024xICaa97ZkMfs6DRKlCguiAI+rbXv5GWwXIkg==` |
| csstype (inlined by upstream dist) | 3.2.3 | MIT | `sha512-z1HGKcYy2xA8AGQfwrn0PAy+PB7X/GSj3UVJW9qKyn43xWa+gl5nXmU4qqLMRzWVLFC8KusUX8T/0kCiOYpAIQ==` |
| pinets | 0.13.0 | AGPL-3.0-only | `sha512-ze+lZZs+j30czY+rpPJ/A69Vd8GmvNsakVpTcS0xsNUQq6W8KjV6aGL8soXi/d0Kax7Jlp0mOUhJ1gW/8ByEuA==` |
| proxy-compare | 3.0.1 | MIT | `sha512-V9plBAt3qjMlS1+nC8771KNf6oJ12gExvaxnNzN/9yVRLdTv/lc+oJlnSzrdYDAvBfTStPCoiaCOTmTs0adv7Q==` |

## Notes

- `pinets` and `@luxalgo/vela-pinets` are AGPL-3.0-only (PineTS is also offered by LuxAlgo under a commercial licence). Whether and how AGPL-3.0 §13 applies to this deployment has not been decided by the project owner; see `backend/SMART_CHART.md` ("Licensing").
- Upstream builds of some packages ship without licence headers, so the bundle cannot carry them inline; the texts and copyright lines are in `THIRD_PARTY_LICENSES.txt`. Legal comments that upstream does ship are retained at the end of the bundle.
- Pine Script and TradingView are trademarks of TradingView, Inc.; PineTS and Vela are independent LuxAlgo projects, not affiliated with or endorsed by TradingView.
