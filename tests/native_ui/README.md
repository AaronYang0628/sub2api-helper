# Native row bridge browser regression

The standalone fixture copies the native v0.2.22 table/card identity and action
structure. It loads the production `helper/static/native.js` and `native.css`,
then exercises mocked same-origin row APIs. No real service or credentials are
used. Native upstream CSS is compiled to a temporary directory for this run;
tracked CSS is untouched.

Requirements: Node.js, `npm --prefix ui ci`, Chromium, and Playwright or
`playwright-core`. Run from the repository root:

```sh
HELPER_PLAYWRIGHT_PATH=/path/to/playwright-core HELPER_CHROMIUM_PATH=/usr/bin/chromium \
  node tests/native_ui/test.cjs
```

Without `HELPER_PLAYWRIGHT_PATH`, the runner tries the installed `playwright-core` package.
Screenshots and temporary CSS are written under a new `/tmp/helper-native-ui-*`
directory, printed when the test completes.
