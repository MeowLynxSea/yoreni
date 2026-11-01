{% if sandbox %}
- Commands run on this Linux machine. Use standard Linux tools and POSIX paths.
- Web pages are automated with the `browser-skill` (`bsk` in the shell) against the local Chromium; the computer tool controls this machine's desktop.
- A file you create becomes visible to the user once it is inside the workspace.
{% elif system == 'Windows' %}
- Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
{% else %}
- Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
{% endif %}
