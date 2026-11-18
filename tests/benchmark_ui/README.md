# Mixed-media benchmark browser regression

Run with the same Chromium and Playwright installation as `tests/native_ui`:

```sh
HELPER_PLAYWRIGHT_PATH=/path/to/playwright-core \
HELPER_CHROMIUM_PATH=/usr/bin/chromium node tests/benchmark_ui/test.cjs
```

The test serves the real admin console and reusable result renderer over a temporary
local HTTP server. Model calls and HTTPS image responses are mocked; no real
credentials or external services are used. It checks retained configuration,
plain-text HTML handling, decoded PNG, an original two-frame GIF, neutral media
scores, explicit external-image loading and retry, plus empty/dark/mobile views.
Screenshots are written to the temporary directory printed on completion.
