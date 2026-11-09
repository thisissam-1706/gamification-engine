(function () {
  "use strict";

  var CAPS = { small: 30, medium: 80, big: 160 };
  var COLORS = ["#5B3DF5", "#FFB020", "#D92D4A", "#2F9E8F", "#3B22C4"];

  function burst(options) {
    options = options || {};
    var size = CAPS[options.size] ? options.size : "small";
    var canvas = document.createElement("canvas");
    var context = canvas.getContext("2d");
    var particles = [];
    var started = performance.now();
    var duration = 2200;
    var origin = options.origin && options.origin.getBoundingClientRect();
    var originX = origin ? origin.left + origin.width / 2 : window.innerWidth / 2;
    var originY = origin ? origin.top + origin.height / 2 : window.innerHeight / 2;

    canvas.className = "confetti-canvas";
    canvas.width = window.innerWidth * window.devicePixelRatio;
    canvas.height = window.innerHeight * window.devicePixelRatio;
    canvas.style.width = window.innerWidth + "px";
    canvas.style.height = window.innerHeight + "px";
    context.scale(window.devicePixelRatio, window.devicePixelRatio);
    document.body.appendChild(canvas);

    for (var i = 0; i < CAPS[size]; i += 1) {
      var angle = Math.random() * Math.PI * 2;
      var speed = 3 + Math.random() * (size === "big" ? 7 : 5);
      particles.push({
        x: originX, y: originY, vx: Math.cos(angle) * speed, vy: Math.sin(angle) * speed - 3,
        size: 4 + Math.random() * 5, rotation: Math.random() * Math.PI,
        spin: (Math.random() - 0.5) * 0.3, color: COLORS[i % COLORS.length]
      });
    }

    function frame(now) {
      var elapsed = now - started;
      context.clearRect(0, 0, window.innerWidth, window.innerHeight);
      particles.forEach(function (particle) {
        particle.x += particle.vx;
        particle.vy += 0.12;
        particle.y += particle.vy;
        particle.rotation += particle.spin;
        context.save();
        context.translate(particle.x, particle.y);
        context.rotate(particle.rotation);
        context.globalAlpha = Math.max(0, 1 - elapsed / duration);
        context.fillStyle = particle.color;
        context.fillRect(-particle.size / 2, -particle.size / 2, particle.size, particle.size * 0.6);
        context.restore();
      });
      if (elapsed < duration) window.requestAnimationFrame(frame);
      else canvas.remove();
    }
    window.requestAnimationFrame(frame);
  }

  window.StreakwiseConfetti = { burst: burst };
})();
