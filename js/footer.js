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
      var links = slot.querySelectorAll("a[href]");
      for (var i = 0; i < links.length; i++) {
        var href = links[i].getAttribute("href");
        if (/^[a-z]+:/i.test(href) || href.charAt(0) === "#") continue;
        links[i].href = new URL(href, root).href;
      }
    });
})();
