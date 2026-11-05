(function () {
  var script = document.currentScript;
  var root = new URL("../", script.src);
  var slot = document.getElementById("site-footer");
  if (!slot) return;

  fetch(new URL("../footer.html", script.src))
    .then(function (response) {
      if (!response.ok) throw new Error("footer " + response.status);
      return response.text();
    })
    .then(function (html) {
      slot.innerHTML = html;
      var links = slot.querySelectorAll("a[href], img[src]");
      for (var i = 0; i < links.length; i++) {
        var attr = links[i].hasAttribute("href") ? "href" : "src";
        var value = links[i].getAttribute(attr);
        if (/^[a-z]+:/i.test(value) || value.charAt(0) === "#") continue;
        links[i].setAttribute(attr, new URL(value, root).href);
      }
    });
})();
