The stylesheet uses the exact component definitions and theme from
[Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api), commit
`15871f904056f548dd874a1d069ffc6cdf777b78`.

- `upstream.css` is an unchanged copy of `frontend/src/style.css`.
- `upstream.tailwind.config.js` is an unchanged copy of `frontend/tailwind.config.js`.
- `adapters.css` maps the static helper DOM to those components and adds its layout.
- `tailwind.config.js` changes only the scanned content paths.

These upstream sources are licensed under LGPL-3.0. See the repository's
third-party notices and license texts for attribution and license information.

Rebuild from the repository root with:

```sh
npm --prefix ui ci
npm --prefix ui run build
```

Commit `helper/static/style.css` after rebuilding. The image serves the generated
CSS directly; Node.js is needed only when changing the UI source.
