// Dibuja los diagramas Mermaid del mapa de la materia (bloques <pre class="mermaid">).
// "strict" limpia el texto y desactiva los clics; sin htmlLabels las etiquetas son texto SVG, nunca HTML.
(function () {
  var dark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  mermaid.initialize({ startOnLoad: true, securityLevel: "strict", htmlLabels: false,
                      flowchart: { htmlLabels: false }, theme: dark ? "dark" : "default" });
})();
