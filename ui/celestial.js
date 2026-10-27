/* Local astronomical approximations; no location request or additional network call.
   Solar equations: https://gml.noaa.gov/grad/solcalc/solareqns.PDF
   Low-order lunar orbital elements: https://aa.quae.nl/en/reken/hemelpositie.html
   Illumination definition: https://aa.usno.navy.mil/faq/moon_phases */
(function (root) {
  'use strict';
  var RAD = Math.PI / 180, DAY = 86400000;
  function clamp(x, low, high) { return Math.max(low, Math.min(high, x)); }
  function wrap(x, period) { return ((x % period) + period) % period; }
  function sun(date, latitude, longitude) {
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || Math.abs(latitude) > 90 || Math.abs(longitude) > 180) return null;
    var year = date.getUTCFullYear(), start = Date.UTC(year, 0, 1);
    var length = (Date.UTC(year + 1, 0, 1) - start) / DAY;
    var minutes = date.getUTCHours() * 60 + date.getUTCMinutes() + date.getUTCSeconds() / 60;
    var ordinal = Math.floor((date.valueOf() - start) / DAY) + 1;
    var g = 2 * Math.PI / length * (ordinal - 1 + (minutes / 60 - 12) / 24);
    var correction = 229.18 * (.000075 + .001868 * Math.cos(g) - .032077 * Math.sin(g) - .014615 * Math.cos(2*g) - .040849 * Math.sin(2*g));
    var declination = .006918 - .399912 * Math.cos(g) + .070257 * Math.sin(g) - .006758 * Math.cos(2*g) + .000907 * Math.sin(2*g) - .002697 * Math.cos(3*g) + .00148 * Math.sin(3*g);
    var hourAngle = wrap((minutes + correction + 4 * longitude) / 4, 360) - 180;
    var lat = latitude * RAD;
    var altitude = Math.asin(clamp(Math.sin(lat) * Math.sin(declination) + Math.cos(lat) * Math.cos(declination) * Math.cos(hourAngle * RAD), -1, 1)) / RAD;
    var horizon = (Math.sin(-.833 * RAD) - Math.sin(lat) * Math.sin(declination)) / (Math.cos(lat) * Math.cos(declination));
    var halfDay = Math.acos(clamp(horizon, -1, 1)) / RAD;
    return { altitude: altitude, hourAngle: hourAngle, progress: halfDay > 0 ? clamp((hourAngle + halfDay) / (2 * halfDay), 0, 1) : 0,
      polarDay: horizon < -1, polarNight: horizon > 1 };
  }
  function moon(date) {
    var days = date.valueOf() / DAY + 2440587.5 - 2451545;
    var solarAnomaly = (357.5291 + .98560028 * days) * RAD;
    var solarLongitude = (280.4663 + .98564736 * days + 1.9148 * Math.sin(solarAnomaly) + .0200 * Math.sin(2 * solarAnomaly)) * RAD;
    var lunarAnomaly = (134.963 + 13.064993 * days) * RAD;
    var lunarLongitude = (218.316 + 13.176396 * days + 6.289 * Math.sin(lunarAnomaly)) * RAD;
    var lunarLatitude = 5.128 * Math.sin((93.272 + 13.229350 * days) * RAD) * RAD;
    var elongation = wrap(lunarLongitude - solarLongitude, 2 * Math.PI);
    var fraction = clamp((1 - Math.cos(lunarLatitude) * Math.cos(elongation)) / 2, 0, 1);
    var waxing = elongation < Math.PI;
    var label = fraction < .015 ? 'Neumond' : fraction > .985 ? 'Vollmond' : Math.abs(fraction - .5) < .025 ? (waxing ? 'Erstes Viertel' : 'Letztes Viertel') : (waxing ? 'Zunehmender Mond' : 'Abnehmender Mond');
    return { fraction: fraction, waxing: waxing, phase: elongation / (2 * Math.PI), label: label };
  }
  function moonPath(fraction, waxing) {
    var edge = 1 - 2 * clamp(fraction, 0, 1), radius = Math.max(.001, Math.abs(edge) * 50);
    return { path: 'M50 0 A50 50 0 0 1 50 100 A' + radius.toFixed(4) + ' 50 0 0 ' + (edge >= 0 ? 0 : 1) + ' 50 0 Z',
      transform: waxing ? '' : 'translate(100 0) scale(-1 1)' };
  }
  var api = { sun: sun, moon: moon, moonPath: moonPath };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.JarvisSky = api;
})(typeof window !== 'undefined' ? window : globalThis);
