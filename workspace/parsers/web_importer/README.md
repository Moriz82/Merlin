# Web importer

Supports HAR, ZAP JSON reports, and Burp XML exports. HTTP/HTTPS URLs are
validated without credentials; query values are omitted from normal views.
HTTP exchanges and scanner alerts become observations only, never findings or
execution requests. Base64 request/response payloads in Burp exports are
checked for quarantine markers and are not unpacked.

Malformed documents, credential-bearing URLs, and unsupported report shapes are
rejected. Tests cover HAR/ZAP/Burp dispatch, URL handling, and quarantine.
