# Gallery administration browser regression

Run with a local Chromium and the same Playwright installation used by
`tests/native_ui` and `tests/benchmark_ui`:

```sh
HELPER_PLAYWRIGHT_PATH=/path/to/playwright-core \
HELPER_CHROMIUM_PATH=/usr/bin/chromium node tests/gallery_admin/test.cjs
```

The fixture serves the real administration assets and a local mock API. It
checks configuration, schedule toggles, manual generation progress, revoked
keys, failed saves, catalog retry, inert names, mobile layout, and timer cleanup.
It also verifies one shared prompt for multiple dynamic groups, comparison
progress, paused membership suppressing individual generation, and exiting a
comparison while keeping its history and restoring individual controls.
It does not call models, external services, or use real API keys.
