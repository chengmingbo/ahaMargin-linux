# Third-party components

The AI assistant's provider definitions, prompts, Markdown and response parsers,
and shell runner are adapted from heehawPDF and heehawPDF-Linux by Mingbo Cheng.
They are distributed under the PolyForm Noncommercial License 1.0.0; see
[the included license](ahamargin/vendor/heehawpdf/LICENSE.md). These components
are not covered by this repository's MIT license.

Required Notice: Copyright 2026 Mingbo Cheng (https://github.com/chengmingbo/heehawPDF)
Required Notice: Copyright 2026 Mingbo Cheng (https://github.com/chengmingbo/heehawPDF-Linux)

Native Qt adaptations remove the browser file helpers, use ahaMargin's name
and keyring service, and host the protocol in QJSEngine. Python handles the
panel, document context, settings, key storage, and annotation integration.
