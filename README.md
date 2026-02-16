# Researcher

### [Demo Website](http://ankitsultana.com/researcher)

A clean, single column, monospace resume template built for jekyll

### Installation

Simply fork the repository and edit away.

#### Installation via remote themes

* Just setting `remote_theme: ankitsultana/researcher@gem` in `_config.yml` should work. Although in that case, I am not sure how
you would build your site locally for testing. If you know how, open up an issue and let me know.
* For more info, [refer this](https://blog.github.com/2017-11-29-use-any-theme-with-github-pages/).

### Customization

* You can edit the `.md` (markdown) files as you see fit. You can also add some other markdown file, say `foo.md` in the root directory of the repository. It will then be accessible like so `{{ url of your website }}/foo`.

* You can of course remove `contact.md` if you don't want it

* To set the heading, edit the `title` variable in `_config.yml`

* To edit the `links` mentioned on the navigation bar, you can edit `_config.yml`. For example:

```
nav:
 - name: "About"
   link: "/researcher/"
 - name: "Resume"
   link: "resume.pdf"
 - name: "Contact"
   link: "contact"
```

* You can change the accent (color of hyperlinks) by editing the `accent` variable in `_sass/vars.scss`

* You can setup google analytics, by setting `tracking_id` in `_config.yml`

* To add a profile picture, make sure to give the image tag the class `profile-picture`. In other words,do it like so:

```html
<img class="profile-picture" src="sherlock.jpg">
```

* You can remove/customize the footer as you like by setting the
appropriate variables in `_config.yml`

* (New in v1.2.0) You can add institute logo at the top, by setting `ins_logo` in `_config.yml`. If you want
to adjust the logo's size, try setting `max-height` in `#ins-logo` in file `./_sass/_style.scss` to the desired
value

![Institute Logo Image Sample](https://github.com/ankitsultana/assets/raw/master/ins-logo-sample.png)

**Note:** Customizing the accent color might cause merge conflicts if you later try to merge from `bk2dcradle/researcher` to fetch updates/patches etc. (applicable only if you have forked).

### License

[GNU GPL v3](https://github.com/bk2dcradle/researcher/blob/gh-pages/LICENSE)

### Auto-update Publications From Google Scholar

This repo includes a script to refresh the `## Publications` section in `index.md` from a Google Scholar profile while preserving your existing markdown style.

1. Edit `scripts/scholar_sync_config.json` as needed:
   - `scholar_user_id`: Google Scholar user id
   - `target_author_last_name`: highlighted in bold in author lists
   - `assets_dir`: local assets folder (default `assets`)
   - `min_year`: only add new entries from this year onward, without rewriting older entries
   - `pdf_overrides`: optional collision map of title -> PDF path/link
2. Run:

```bash
python3 scripts/update_publications.py
```

The script updates only the `## Publications` section in `index.md`.  
With `min_year` set, it preserves existing entries and prepends only new items from that year onward.

PDF link behavior for new items:
- If `assets/{first_author_last_name}_{year}.pdf` exists (example: `assets/behera_2026.pdf`), it adds `[[PDF]](...)`.
- Otherwise it adds `[[Scholar]](...)`.
- Use `pdf_overrides` only when you need to disambiguate collisions.
