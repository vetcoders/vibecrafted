  function mdToHtml(src) {
    var text = String(src || "").replace(/\r\n/g, "\n");
    var occCounter = { n: 0 };

    // Extract fenced code first (placeholders survive block scan).
    var fences = [];
    text = text.replace(/```([^\n`]*)\n([\s\S]*?)```/g, function (_, lang, body) {
      var i = fences.length;
      fences.push(
        '<pre class="md-code"><code' +
          (lang && lang.trim()
            ? ' data-lang="' + esc(lang.trim()) + '"'
            : "") +
          ">" +
          esc(body.replace(/\n$/, "")) +
          "</code></pre>"
      );
      return "\n%%FENCE" + i + "%%\n";
    });

    var lines = text.split("\n");
    var out = [];
    var i = 0;

    // YAML frontmatter: leading --- ... ---
    if (lines[0] === "---") {
      var fm = [];
      i = 1;
      while (i < lines.length && lines[i] !== "---") {
        fm.push(lines[i]);
        i++;
      }
      if (i < lines.length && lines[i] === "---") i++;
      var rows = fm
        .filter(function (l) {
          return l.trim().length;
        })
        .map(function (l) {
          var colon = l.indexOf(":");
          if (colon < 0) {
            return (
              '<div class="md-fm-row"><span class="md-fm-val">' +
              inline(l, occCounter) +
              "</span></div>"
            );
          }
          return (
            '<div class="md-fm-row"><span class="md-fm-key">' +
            esc(l.slice(0, colon).trim()) +
            '</span><span class="md-fm-val">' +
            inline(l.slice(colon + 1).trim(), occCounter) +
            "</span></div>"
          );
        })
        .join("");
      out.push('<section class="md-frontmatter">' + rows + "</section>");
    }

    function isTableSep(line) {
      return /^\s*\|?[\s:|-]+\|[\s|:|-]+\|?\s*$/.test(line) && /\|/.test(line) && /-/.test(line);
    }
    function isTableRow(line) {
      return /\|/.test(line) && !isTableSep(line);
    }
    function splitRow(line) {
      var s = line.trim();
      if (s.charAt(0) === "|") s = s.slice(1);
      if (s.charAt(s.length - 1) === "|") s = s.slice(0, -1);
      return s.split("|").map(function (c) {
        return c.trim();
      });
    }

    while (i < lines.length) {
      var line = lines[i];
      var fence = line.match(/^%%FENCE(\d+)%%$/);
      if (fence) {
        out.push(fences[Number(fence[1])]);
        i++;
        continue;
      }
      if (/^\s*$/.test(line)) {
        i++;
        continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        var lvl = h[1].length;
        out.push(
          "<h" + lvl + ">" + inline(h[2], occCounter) + "</h" + lvl + ">"
        );
        i++;
        continue;
      }
      if (/^(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        out.push("<hr>");
        i++;
        continue;
      }
      // GFM table: header + separator + body rows
      if (
        isTableRow(line) &&
        i + 1 < lines.length &&
        isTableSep(lines[i + 1])
      ) {
        var header = splitRow(line);
        i += 2;
        var bodyRows = [];
        while (i < lines.length && isTableRow(lines[i])) {
          bodyRows.push(splitRow(lines[i]));
          i++;
        }
        var thead =
          "<thead><tr>" +
          header
            .map(function (c) {
              return "<th>" + inline(c, occCounter) + "</th>";
            })
            .join("") +
          "</tr></thead>";
        var tbody =
          "<tbody>" +
          bodyRows
            .map(function (row) {
              return (
                "<tr>" +
                row
                  .map(function (c) {
                    return "<td>" + inline(c, occCounter) + "</td>";
                  })
                  .join("") +
                "</tr>"
              );
            })
            .join("") +
          "</tbody>";
        out.push('<div class="md-table-wrap"><table class="md-table">' + thead + tbody + "</table></div>");
        continue;
      }
      if (/^>\s?/.test(line)) {
        var q = [];
        while (i < lines.length && /^>\s?/.test(lines[i])) {
          q.push(lines[i].replace(/^>\s?/, ""));
          i++;
        }
        // Preserve newlines inside quotes as <br>, never flatten to one line.
        out.push(
          "<blockquote>" +
            q
              .map(function (ql) {
                return inline(ql, occCounter);
              })
              .join("<br>") +
            "</blockquote>"
        );
        continue;
      }
      if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
        var ordered = /^\s*\d+\.\s+/.test(line);
        var tag = ordered ? "ol" : "ul";
        var items = [];
        while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
          var rawItem = lines[i];
          var task = rawItem.match(/^(\s*[-*+]\s+)\[([ xX~!?])\]\s+(.*)$/);
          if (task) {
            var mark = normalizeMark(task[2]);
            var chip = statusChip(mark, occCounter.n++);
            items.push(
              '<li class="md-task md-task-' +
                (mark === " " ? "todo" : mark === "?" ? "maybe" : mark === "!" ? "blocked" : mark === "~" ? "run" : "done") +
                '">' +
                chip +
                " " +
                inline(task[3], occCounter) +
                "</li>"
            );
          } else {
            items.push(
              "<li>" +
                inline(rawItem.replace(/^\s*([-*+]|\d+\.)\s+/, ""), occCounter) +
                "</li>"
            );
          }
          i++;
        }
        out.push("<" + tag + ' class="md-list">' + items.join("") + "</" + tag + ">");
        continue;
      }
      // Paragraph: keep line breaks as <br> (do NOT join with spaces — that
      // is what flattened YAML/table leftovers into one soup).
      var para = [];
      while (
        i < lines.length &&
        !/^\s*$/.test(lines[i]) &&
        !/^%%FENCE\d+%%$/.test(lines[i]) &&
        !/^(#{1,6})\s+/.test(lines[i]) &&
        !/^>\s?/.test(lines[i]) &&
        !/^\s*([-*+]|\d+\.)\s+/.test(lines[i]) &&
        !/^(-{3,}|\*{3,}|_{3,})\s*$/.test(lines[i]) &&
        !(
          isTableRow(lines[i]) &&
          i + 1 < lines.length &&
          isTableSep(lines[i + 1])
        )
      ) {
        para.push(lines[i]);
        i++;
      }
      out.push(
        "<p>" +
          para
            .map(function (pl) {
              return inline(pl, occCounter);
            })
            .join("<br>") +
          "</p>"
      );
    }
    return out.join("\n");

    function inline(s, counter) {
      // Lift status tokens first so backtick/code pass does not eat them.
      var parts = [];
      var re = new RegExp(STATUS_RE.source, "g");
      var last = 0;
      var m;
      var str = String(s);
      while ((m = re.exec(str)) !== null) {
        if (m.index > last) parts.push({ t: "text", v: str.slice(last, m.index) });
        parts.push({ t: "status", mark: normalizeMark(m[1]), occ: counter.n++ });
        last = m.index + m[0].length;
      }
      if (last < str.length) parts.push({ t: "text", v: str.slice(last) });
      if (!parts.length) parts.push({ t: "text", v: str });

      return parts
        .map(function (p) {
          if (p.t === "status") return statusChip(p.mark, p.occ);
          var t = esc(p.v);
          t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
          t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
          t = t.replace(/(^|[^\*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
          t = t.replace(
            /\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
            '<a href="$2" rel="noopener noreferrer" target="_blank">$1</a>'
          );
          return t;
        })
        .join("");
    }
  }

