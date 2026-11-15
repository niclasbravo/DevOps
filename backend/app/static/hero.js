// Entrada animada del titular con GSAP (ESM desde CDN). Es un extra: si la librería no carga,
// o la persona pide menos movimiento, la página se ve y funciona igual, sin animación.
// La clase "enter" la pone index.html antes del primer pintado y oculta el titular hasta que entra.

const root = document.documentElement;
const show = () => root.classList.remove("enter");

if (root.classList.contains("enter")) {
  const safety = setTimeout(show, 2500);
  try {
    const mod = await import("https://cdn.jsdelivr.net/npm/gsap@3.12.5/+esm");
    const gsap = mod.gsap || mod.default;
    const panel = document.querySelector('[role="tabpanel"]:not([hidden])');
    const words = panel.querySelectorAll(".sentence-word, .combo");
    const rest = [
      document.querySelector(".mode-switch"),
      ...panel.querySelectorAll(".help, .suggestions"),
      document.querySelector(".actions"),
    ];
    clearTimeout(safety);
    gsap.set([...words, ...rest], { opacity: 0 }); // el ocultado pasa a estilos en línea…
    show(); // …y la clase ya no hace falta
    // Solo transform y opacity.
    gsap
      .timeline({ defaults: { ease: "expo.out" } })
      .fromTo(
        words,
        { opacity: 0, yPercent: 45 },
        { opacity: 1, yPercent: 0, duration: 0.9, stagger: 0.1, clearProps: "opacity,transform" },
      )
      .fromTo(
        rest,
        { opacity: 0, y: 12 },
        { opacity: 1, y: 0, duration: 0.6, stagger: 0.07, clearProps: "opacity,transform" },
        "-=0.55",
      );
  } catch {
    clearTimeout(safety);
    show();
  }
}
