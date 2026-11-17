# Third-party UI source

The frontend includes styles, Tailwind design tokens, and SVG icon paths derived
from [Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api), reviewed at commit
`15871f904056f548dd874a1d069ffc6cdf777b78`.

Upstream files: `frontend/src/style.css`, `frontend/tailwind.config.js`, and
`frontend/src/components/icons/Icon.vue`. These components are licensed under
GNU LGPL version 3 or later; see [the retained license](LICENSES/sub2api-LGPL-3.0.txt).
The LGPL incorporates the GPL version 3; its text is retained alongside it.
Upstream attribution and original source are retained in `ui/` and in the copied
icon module. Helper-specific layout adapters do not include sub2api's outer app
header or sidebar. Rebuild instructions and dependency lockfile are in `ui/`.

The generated stylesheet is included in the Docker image with the corresponding
upstream style source, adapters, build configuration, and license notices so it
can be regenerated or replaced. Other third-party packages retain their own
licenses in their installed distributions.
