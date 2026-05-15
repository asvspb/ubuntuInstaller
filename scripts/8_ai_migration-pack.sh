# MASTER PROMPT: 100% Exact Copy of "applet"

This prompt contains the COMPLETE source code of the application **applet**.
Send it to any AI agent to recreate the project **identically**.

---

## Project Info

- **Project:** applet
- **Auto-detected language:** node
- **Capture timestamp:** 2026-05-15T09:04:55Z

## First Steps for the AI Agent

1. Parse every `<file path="...">` block below
2. Extract content from `<![CDATA[ ... ]]>` (the `]]>` inside code has been escaped — undo when writing)
3. Recreate the file at the exact path
4. After ALL files are created, run: `npm install`
5. Then start the dev server: `vite --port=3000 --host=0.0.0.0`

---

## RULES — YOU MUST OBEY ALL OF THEM

1. **NO MODIFICATIONS:** Recreate every file character-for-character. Do not refactor, optimize, or modernize. Keep ALL comments (including non-English), ALL unused variables, ALL console.log statements, ALL commented-out code.

2. **CHUNK YOUR WORK [IMPORTANT]:** If there are more than 7 files, do NOT try to create them all at once. Create 7 files using your tools in parallel, then STOP and ask the user to type "continue". Repeat until ALL files are created. This avoids hitting output token limits.

3. **USE YOUR FILE TOOLS SILENTLY:** If you have file-editing tools (create_file, write_to_file, bash), use them. Do NOT print code blocks in chat — just execute and confirm.

4. **BIG FILE STRATEGY:** If a file inside CDATA is too large for one tool call, use append/write in chunks or generate via a script. Do not truncate.

5. **INSTALL FIRST:** Create package.json (or equivalent) and install dependencies BEFORE starting the server.

6. **EXACT PATHS:** Respect `path="..."` exactly. Auto-create parent directories.

7. **BINARY PLACEHOLDERS [CRITICAL]:** For skipped binary files (marked as `<!-- SKIP (binary): ... -->`), create VALID minimal placeholders. A 0-byte .png or .ico will crash Vite/Webpack.
   - For images (.png, .jpg, .ico): create a 1x1 transparent pixel
   - Use this bash snippet for a valid 1x1 transparent PNG:
     ```bash
     printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0bIDATx\x9cc\x62\x08\x05\x00\x00\x00\x00\xff\xff\x03\x00\x00\x04\x00\x01\x0c\x0c\x0c\x0c\x00\x00\x00\x00IEND\xaeB`\x82' > path/to/file.png
     ```

8. **EXACT VERSIONS:** Use dependency versions as specified. Do not upgrade.

9. **CDATA ESCAPE FIX:** Content has `]]>` escaped as `]]]]><![CDATA[>`. Reverse this when writing: replace `]]]]><![CDATA[>` back to `]]>`.

10. **VERIFY:** After setup, print "MIGRATION COMPLETE — all 9 files created" when done.

---

## PRO TIP: Fast Migration with Self-Extracting Script

Instead of calling file tools 50+ times, write a **single extraction script**:

**Option A: Python unpacker** (best)
- Read the XML `<file>` blocks below
- Parse `path` attr and `CDATA` content
- Handle `]]]]><![CDATA[>` → `]]>` unescaping
- Create all files and directories
- Touch valid placeholders for skipped binaries

**Option B: Node.js unpacker** (if Node project)
Same logic in JS.

**Option C: Direct tool calls** (fallback)
Create 7 files at a time, ask "continue" between chunks.

---

## Source Code

Below is every source file, wrapped in XML with CDATA for safe parsing.

## Project Metadata

- **Name:** applet
- **Language:** node
- **Framework:** react
- **Captured at:** 2026-05-15T09:04:55Z
- **Total files:** 9
- **Skipped binary:** 0
- **Install command:** `npm install`
- **Dev command:** `vite --port=3000 --host=0.0.0.0`

### Directory Structure