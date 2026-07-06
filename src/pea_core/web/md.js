/* 三体 PEA 共享：轻量 Markdown → HTML 渲染器（无依赖，单一真源，各 PEA 由 /core/md.js 引用）。
   支持：标题/粗体/斜体/行内代码/代码块/引用/分割线/有序无序列表/表格/链接。window.mdToHtml(md)。 */
(function () {
  function esc(s) { return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }
  function inline(s) {
    s = esc(s);
    s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\s][^*]*?)\*/g, "$1<em>$2</em>");
    s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return s;
  }
  function buildTable(rows) {
    var cells = rows.map(function (r) {
      return r.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return c.trim(); });
    });
    var html = "<table><thead><tr>" + cells[0].map(function (c) { return "<th>" + inline(c) + "</th>"; }).join("") + "</tr></thead><tbody>";
    for (var i = 2; i < cells.length; i++) {
      html += "<tr>" + cells[i].map(function (c) { return "<td>" + inline(c) + "</td>"; }).join("") + "</tr>";
    }
    return html + "</tbody></table>";
  }
  function mdToHtml(md) {
    var lines = String(md || "").replace(/\r/g, "").split("\n");
    var out = [], i = 0;
    while (i < lines.length) {
      var line = lines[i];
      if (/^```/.test(line)) {
        var buf = []; i++;
        while (i < lines.length && !/^```/.test(lines[i])) { buf.push(esc(lines[i])); i++; }
        i++; out.push("<pre><code>" + buf.join("\n") + "</code></pre>"); continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]*-[\s:|-]*$/.test(lines[i + 1])) {
        var tb = []; while (i < lines.length && /^\s*\|.*\|?\s*$/.test(lines[i])) { tb.push(lines[i]); i++; }
        out.push(buildTable(tb)); continue;
      }
      var h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { out.push("<h" + h[1].length + ">" + inline(h[2]) + "</h" + h[1].length + ">"); i++; continue; }
      if (/^\s*([-*_])\s*\1\s*\1[\s\1]*$/.test(line)) { out.push("<hr>"); i++; continue; }
      if (/^\s*>\s?/.test(line)) {
        var bq = []; while (i < lines.length && /^\s*>\s?/.test(lines[i])) { bq.push(lines[i].replace(/^\s*>\s?/, "")); i++; }
        out.push("<blockquote>" + inline(bq.join(" ")) + "</blockquote>"); continue;
      }
      if (/^\s*[-*+]\s+/.test(line)) {
        var ul = []; while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) { ul.push("<li>" + inline(lines[i].replace(/^\s*[-*+]\s+/, "")) + "</li>"); i++; }
        out.push("<ul>" + ul.join("") + "</ul>"); continue;
      }
      if (/^\s*\d+\.\s+/.test(line)) {
        var ol = []; while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) { ol.push("<li>" + inline(lines[i].replace(/^\s*\d+\.\s+/, "")) + "</li>"); i++; }
        out.push("<ol>" + ol.join("") + "</ol>"); continue;
      }
      if (/^\s*$/.test(line)) { i++; continue; }
      var p = [];
      while (i < lines.length && !/^\s*$/.test(lines[i]) &&
        !/^(#{1,6}\s|```|\s*>|\s*[-*+]\s|\s*\d+\.\s|\s*\|)/.test(lines[i])) { p.push(lines[i]); i++; }
      // 先逐行 inline（含转义），再用真正的 <br> 连接——否则 <br> 会被转义成字面文本
      out.push("<p>" + p.map(inline).join("<br>") + "</p>");
    }
    return out.join("");
  }
  window.mdToHtml = mdToHtml;
})();
