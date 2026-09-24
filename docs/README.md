# GitHub Pages

`docs/index.html` is the interactive département explorer. It is **generated** — do not edit it by hand:

```bash
python scripts/build_department_explorer.py
```

The file is standalone (Plotly.js, geometry and data embedded), so it can also be opened directly in a browser.

## Publish

1. Commit and push `docs/index.html` to the `main` branch.
2. On GitHub, open the repository **Settings → Pages**.
3. Under **Build and deployment**, set **Source** to **Deploy from a branch**.
4. Select the branch **`main`** and the folder **`/docs`**, then **Save**.
5. After a minute or two the page is live at `https://<user>.github.io/<repository>/`
   (here: `https://clarasalas.github.io/strategic-voting-geo-2RS/`).

## Update

Rebuild with the command above, commit the new `docs/index.html` and push: GitHub Pages redeploys automatically.
No GitHub Actions workflow is needed.
