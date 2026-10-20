"use strict";
// Render conversation formatting with DOM nodes. Message HTML is always text.
window.ChatMarkdown = (() => {
  const node = (tag, text, className) => {
    const result = document.createElement(tag);
    if (text !== undefined) result.textContent = text;
    if (className) result.className = className;
    return result;
  };

  function inline(parent, text, depth = 0) {
    if (depth > 6) {parent.append(document.createTextNode(text));return;}
    const tokens = /\\[\\`*_[\]()#>|-]|`[^`\n]+`|\[[^\]\n]+\]\((?:[^()\n]|\([^()\n]*\))*\)|\*\*[^*\n]+\*\*|(?<![\w])__[^_\n]+__(?![\w])|~~[^~\n]+~~|\*[^*\n]+\*|(?<![\w])_[^_\n]+_(?![\w])/g;
    let cursor = 0;
    for (const match of text.matchAll(tokens)) {
      parent.append(document.createTextNode(text.slice(cursor, match.index)));
      const token = match[0]; let result;
      if (token[0] === "\\") result = document.createTextNode(token.slice(1));
      else if (token[0] === "`") result = node("code", token.slice(1, -1));
      else if (token[0] === "[") {
        const split = token.lastIndexOf("]("), label = token.slice(1, split);
        const target = token.slice(split + 2, -1).trim().replace(/^<|>$/g, "");
        let url;
        try {url = new URL(target);} catch (_) {}
        if (url && ["http:", "https:"].includes(url.protocol)) {
          result = node("a"); result.href = url.href; result.target = "_blank"; result.rel = "noopener noreferrer";
        } else {result = node("span"); result.title = target;}
        inline(result, label, depth + 1);
      } else {
        const double = token.startsWith("**") || token.startsWith("__") || token.startsWith("~~");
        result = node(token.startsWith("~~") ? "del" : double ? "strong" : "em");
        inline(result, token.slice(double ? 2 : 1, double ? -2 : -1), depth + 1);
      }
      parent.append(result); cursor = match.index + token.length;
    }
    parent.append(document.createTextNode(text.slice(cursor)));
  }

  function cells(line) {
    return line.trim().replace(/^\|/, "").replace(/\|$/, "").replace(/\\\|/g, "\u0000").split("|").map(value => value.trim().replace(/\u0000/g, "|"));
  }
  const separator = line => line.includes("|") && cells(line).every(value => /^:?-{3,}:?$/.test(value));
  const bullet = line => /^( {0,12})([-+*]|\d+[.)])\s+(.+)$/.exec(line);
  const fence = line => /^ {0,3}(`{3,}|~{3,})\s*([\w+-]*)\s*$/.exec(line);
  const boundary = line => !line.trim() || fence(line) || /^ {0,3}#{1,6}\s+/.test(line) || /^\s*>/.test(line) || bullet(line) || /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(line);

  function render(parent, text, depth = 0) {
    if (depth > 6) {parent.append(node("p", text)); return;}
    const lines = text.replace(/\r\n?/g, "\n").split("\n"); let index = 0;
    while (index < lines.length) {
      if (!lines[index].trim()) {index++;continue;}
      const opening = fence(lines[index]);
      if (opening) {
        index++; const source = [];
        const closing = new RegExp("^ {0,3}" + opening[1][0] + "{" + opening[1].length + ",}\\s*$");
        while (index < lines.length && !closing.test(lines[index])) source.push(lines[index++]);
        if (index < lines.length) index++;
        const block = node("div", undefined, "code-block"), header = node("div", undefined, "code-heading");
        const copy = node("button", "Copy code", "copy-code"); copy.type = "button";
        copy.addEventListener("click", async () => {
          try {await navigator.clipboard.writeText(source.join("\n"));copy.textContent = "Copied";}
          catch (_) {copy.textContent = "Select to copy";}
          setTimeout(() => {copy.textContent = "Copy code";}, 1800);
        });
        header.append(node("span", opening[2] || "Code"), copy);
        const pre = node("pre"); pre.append(node("code", source.join("\n")));
        block.append(header, pre); parent.append(block); continue;
      }
      const heading = /^ {0,3}(#{1,6})\s+(.+?)(?:\s+#+)?$/.exec(lines[index]);
      if (heading) {const result = node("h" + heading[1].length); inline(result, heading[2]); parent.append(result);index++;continue;}
      if (index + 1 < lines.length && lines[index].includes("|") && separator(lines[index + 1])) {
        const wrapper = node("div", undefined, "table-scroll"), table = node("table"), head = node("thead"), row = node("tr");
        const labels = cells(lines[index]);
        for (const label of labels) {const cell = node("th");inline(cell, label);row.append(cell);}
        head.append(row);table.append(head); index += 2;
        const body = node("tbody");
        while (index < lines.length && lines[index].trim() && lines[index].includes("|")) {
          const values = cells(lines[index++]), row = node("tr");
          for (let i = 0; i < labels.length; i++) {const cell = node("td");inline(cell, values[i] || "");row.append(cell);}
          body.append(row);
        }
        table.append(body);wrapper.append(table);parent.append(wrapper);continue;
      }
      if (/^\s*>/.test(lines[index])) {
        const content = [];
        while (index < lines.length && /^\s*>/.test(lines[index])) content.push(lines[index++].replace(/^\s*> ?/, ""));
        const quote = node("blockquote");render(quote, content.join("\n"), depth + 1);parent.append(quote);continue;
      }
      if (/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/.test(lines[index])) {parent.append(node("hr"));index++;continue;}
      const first = bullet(lines[index]);
      if (first) {
        const ordered = /^\d/.test(first[2]), list = node(ordered ? "ol" : "ul");
        if (ordered) list.start = parseInt(first[2], 10);
        while (index < lines.length) {
          const match = bullet(lines[index]);
          if (!match || /^\d/.test(match[2]) !== ordered) break;
          const item = node("li");item.className = "indent-" + Math.min(6, Math.floor(match[1].length / 2));
          const content = match[3];
          inline(item, content.replace(/^\[([ xX])\]\s+/, (_, checked) => checked.toLowerCase() === "x" ? "☑ " : "☐ "));
          list.append(item);index++;
          while (index < lines.length && /^ {2,}\S/.test(lines[index]) && !bullet(lines[index]) && !fence(lines[index])) {
            item.append(document.createTextNode("\n"));inline(item, lines[index++].trim());
          }
        }
        parent.append(list);continue;
      }
      const paragraph = [lines[index++]];
      while (index < lines.length && !boundary(lines[index]) && !(index + 1 < lines.length && separator(lines[index + 1]))) paragraph.push(lines[index++]);
      const result = node("p");inline(result, paragraph.join("\n"));parent.append(result);
    }
  }
  return {render};
})();
