{% if sandbox %}
- Commands run in a Linux VM. Use standard Linux tools and POSIX paths.
- The browser tool drives Chromium in the same VM; the computer tool controls the VM desktop.
- A file you create is visible to the user in the WebUI only after it exists in the VM workspace.
{% elif system == 'Windows' %}
- Do not assume GNU tools like `grep`, `sed`, or `awk` exist.
- Prefer Windows-native commands or file tools when they are more reliable.
- If terminal output is garbled, retry with UTF-8 output enabled.
{% else %}
- Prefer UTF-8 and standard shell tools.
- Use file tools when they are simpler or more reliable than shell commands.
{% endif %}
