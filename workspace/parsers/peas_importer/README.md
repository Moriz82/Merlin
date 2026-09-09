# PEAS importer

Supports LinPEAS and WinPEAS text evidence indexes when the corresponding tool
signature is present. ANSI display controls are removed before bounded section
observations are produced. The importer deliberately emits a partial-evidence
limitation and makes no privilege or vulnerability conclusion.

Unsupported or unsigned text is rejected for manual evidence review. No PEAS
tool is executed and no host files or network endpoints are accessed.
