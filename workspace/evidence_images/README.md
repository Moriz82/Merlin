# Evidence image preview

Use this module only for authenticated local evidence. It identifies PNG, GIF, and JPEG files from their bytes. It rejects SVG, HTML, unknown formats, files above 16 MiB, and unsafe image dimensions.

The route checks the stored size and SHA-256 value before it returns a file. It does not convert the image or contact another host. Keep external images disabled in Markdown.
