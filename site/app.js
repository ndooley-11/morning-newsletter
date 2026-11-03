(function () {
  'use strict';

  var CONTENT_URL = './content.json';
  var MAX_RESPONSE_LENGTH = 300000;
  var DEFAULT_SLIDE_SECONDS = 12;
  var MIN_SLIDE_SECONDS = 8;
  var MAX_SLIDE_SECONDS = 60;
  var DEFAULT_REFRESH_SECONDS = 300;
  var MIN_REFRESH_SECONDS = 60;
  var MAX_REFRESH_SECONDS = 3600;
  var WORDS_PER_CHUNK = 42;
  var MAX_WEATHER_CARDS = 2;
  var MAX_SLIDES = 100;

  var LEN = { headline: 250, summary: 12000, caption: 2000, text: 1000 };

  var IMAGE_SRC_PATTERN = /^assets\/photos\/[a-z0-9][a-z0-9-]{0,59}\.jpg$/;
  var ISO_DATE_PATTERN = /^\d{4}-\d{2}-\d{2}$/;

  var els = {};

  var state = {
    editionId: null,
    renderSlides: [],
    currentIndex: 0,
    paused: false,
    secondsPerSlide: DEFAULT_SLIDE_SECONDS,
    refreshSeconds: DEFAULT_REFRESH_SECONDS,
    loadedOnce: false,
    focusSuspend: false
  };

  var slideTimerId = null;
  var refreshTimerId = null;
  var fetchInFlight = null;
  var currentEdition = null;
  var updateFailure = false;
  var freshnessDeadline = 0;
  var durationOverride = null;

  function editionStatus() {
    if (!currentEdition) return;
    var parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
    }).formatToParts(new Date());
    var dateParts = {};
    parts.forEach(function (p) { dateParts[p.type] = p.value; });
    var today = dateParts.year + '-' + dateParts.month + '-' + dateParts.day;
    var prefix = currentEdition.isExample ? 'Sample edition' :
      (currentEdition.date < today || Date.now() > freshnessDeadline ? 'STALE — last successful edition' :
        (currentEdition.date > today ? 'FUTURE-DATED — check edition clock' : 'Today’s edition'));
    setStatus(prefix + ' — ' + currentEdition.date +
      (updateFailure ? ' · Update failed; retained last good edition' : ''));
  }

  function qs(id) {
    return document.getElementById(id);
  }

  function cacheEls() {
    els.tvFrame = qs('tv-frame');
    els.editionTitle = qs('edition-title');
    els.editionDate = qs('edition-date');
    els.editionStatus = qs('edition-status');
    els.weatherStrip = qs('weather-strip');
    els.currentSection = qs('current-section');
    els.slideCounter = qs('slide-counter');
    els.slideStage = qs('slide-stage');
    els.btnPrev = qs('btn-prev');
    els.btnPause = qs('btn-pause');
    els.btnNext = qs('btn-next');
    els.btnReload = qs('btn-reload');
    els.btnFullscreen = qs('btn-fullscreen');
    els.durationSelect = qs('duration-select');
  }

  // ---------- Validation helpers ----------

  function isNonEmptyString(v, maxLen) {
    return typeof v === 'string' && v.length > 0 && v.length <= maxLen;
  }

  function isBoundedString(v, maxLen) {
    return typeof v === 'string' && v.length <= maxLen;
  }

  function isValidIsoDate(s) {
    if (typeof s !== 'string' || !ISO_DATE_PATTERN.test(s)) return false;
    var parts = s.split('-');
    var y = parseInt(parts[0], 10);
    var m = parseInt(parts[1], 10);
    var d = parseInt(parts[2], 10);
    var dt = new Date(Date.UTC(y, m - 1, d));
    return dt.getUTCFullYear() === y && dt.getUTCMonth() === m - 1 && dt.getUTCDate() === d;
  }

  function isSafeHttpsUrl(s) {
    if (typeof s !== 'string' || s.length === 0 || s.length > LEN.text) return null;
    var u;
    try {
      u = new URL(s);
    } catch (e) {
      return null;
    }
    if (u.protocol !== 'https:') return null;
    if (!u.hostname) return null;
    if (u.username || u.password) return null;
    return u.href;
  }

  function isValidImageSrc(s) {
    return typeof s === 'string' && IMAGE_SRC_PATTERN.test(s);
  }

  // ---------- Top-level validation ----------

  function validateContent(json) {
    if (typeof json !== 'object' || json === null || Array.isArray(json)) {
      throw new Error('Content must be a JSON object.');
    }
    if (json.schemaVersion !== 1) {
      throw new Error('Unsupported or missing schemaVersion.');
    }
    var edition = validateEdition(json.edition);
    var playback = validatePlayback(json.playback);
    var weather = validateWeather(json.weather);
    var slides = validateSlides(json.slides);
    return { edition: edition, playback: playback, weather: weather, slides: slides };
  }

  function validateEdition(e) {
    if (typeof e !== 'object' || e === null || Array.isArray(e)) {
      throw new Error('edition must be an object.');
    }
    if (!isNonEmptyString(e.id, LEN.text)) throw new Error('edition.id is invalid.');
    if (!isValidIsoDate(e.date)) throw new Error('edition.date is invalid; expected YYYY-MM-DD.');
    if (!isNonEmptyString(e.title, LEN.text)) throw new Error('edition.title is invalid.');
    var subtitle = isBoundedString(e.subtitle, LEN.text) ? e.subtitle : '';
    var timezone = isBoundedString(e.timezone, LEN.text) ? e.timezone : '';
    var isExample = typeof e.isExample === 'boolean' ? e.isExample : false;
    var generatedAt = isBoundedString(e.generatedAt, LEN.text) ? e.generatedAt : '';
    return {
      id: e.id,
      date: e.date,
      title: e.title,
      subtitle: subtitle,
      timezone: timezone,
      isExample: isExample,
      generatedAt: generatedAt
    };
  }

  function validatePlayback(p) {
    var seconds = DEFAULT_SLIDE_SECONDS;
    var refresh = DEFAULT_REFRESH_SECONDS;
    if (typeof p === 'object' && p !== null && !Array.isArray(p)) {
      if (typeof p.secondsPerSlide === 'number' && isFinite(p.secondsPerSlide)) {
        seconds = Math.min(MAX_SLIDE_SECONDS, Math.max(MIN_SLIDE_SECONDS, Math.round(p.secondsPerSlide)));
      }
      if (typeof p.refreshSeconds === 'number' && isFinite(p.refreshSeconds)) {
        refresh = Math.min(MAX_REFRESH_SECONDS, Math.max(MIN_REFRESH_SECONDS, Math.round(p.refreshSeconds)));
      }
    }
    return { secondsPerSlide: seconds, refreshSeconds: refresh };
  }

  function validateWeather(arr) {
    if (!Array.isArray(arr)) return [];
    var out = [];
    for (var i = 0; i < arr.length && out.length < MAX_WEATHER_CARDS; i++) {
      var w = arr[i];
      if (typeof w !== 'object' || w === null || Array.isArray(w)) continue;
      if (!isNonEmptyString(w.location, LEN.text)) continue;
      if (!isNonEmptyString(w.summary, LEN.text)) continue;
      var asOf = isBoundedString(w.asOf, 100) && isFinite(Date.parse(w.asOf)) ? w.asOf : '';
      out.push({ location: w.location, summary: w.summary, asOf: asOf });
    }
    return out;
  }

  function validateSlides(arr) {
    if (!Array.isArray(arr) || arr.length < 1 || arr.length > MAX_SLIDES) {
      throw new Error('slides must be an array of 1 to 100 items.');
    }
    var seenIds = Object.create(null);
    var out = [];
    for (var i = 0; i < arr.length; i++) {
      var raw = arr[i];
      if (typeof raw !== 'object' || raw === null || Array.isArray(raw)) {
        throw new Error('slide[' + i + '] must be an object.');
      }
      if (!isNonEmptyString(raw.id, LEN.text)) {
        throw new Error('slide[' + i + '].id is invalid.');
      }
      if (seenIds[raw.id]) {
        throw new Error('slide[' + i + '].id is a duplicate: ' + raw.id);
      }
      seenIds[raw.id] = true;

      if (raw.type === 'story') {
        out.push(validateStorySlide(raw, i));
      } else if (raw.type === 'comic') {
        out.push(validateComicSlide(raw, i));
      } else {
        throw new Error('slide[' + i + '].type is unsupported: ' + String(raw.type));
      }
    }
    return out;
  }

  function validateStorySlide(raw, i) {
    if (!isNonEmptyString(raw.desk, LEN.text)) throw new Error('slide[' + i + '].desk is invalid.');
    if (!isNonEmptyString(raw.section, LEN.text)) throw new Error('slide[' + i + '].section is invalid.');
    if (!isNonEmptyString(raw.headline, LEN.headline)) throw new Error('slide[' + i + '].headline is invalid.');
    var summary = isBoundedString(raw.summary, LEN.summary) ? raw.summary : '';

    var source = null;
    if (typeof raw.source === 'object' && raw.source !== null && !Array.isArray(raw.source)) {
      var label = isNonEmptyString(raw.source.label, LEN.text) ? raw.source.label : null;
      var url = isSafeHttpsUrl(raw.source.url);
      if (label && url) {
        var asOf = isBoundedString(raw.source.asOf, 100) && isFinite(Date.parse(raw.source.asOf)) ? raw.source.asOf : '';
        source = { label: label, url: url, asOf: asOf };
      }
    }

    var image = null;
    if (typeof raw.image === 'object' && raw.image !== null && !Array.isArray(raw.image)) {
      var img = raw.image;
      if (isValidImageSrc(img.src) && isNonEmptyString(img.alt, LEN.text)) {
        image = {
          src: img.src,
          alt: img.alt,
          caption: isBoundedString(img.caption, LEN.caption) ? img.caption : '',
          credit: isBoundedString(img.credit, LEN.text) ? img.credit : '',
          license: isBoundedString(img.license, LEN.text) ? img.license : '',
          sourceUrl: isSafeHttpsUrl(img.sourceUrl),
          licenseUrl: isSafeHttpsUrl(img.licenseUrl),
          changes: isBoundedString(img.changes, LEN.text) ? img.changes : '',
          isArchive: typeof img.isArchive === 'boolean' ? img.isArchive : false
        };
      }
    }

    return {
      id: raw.id,
      type: 'story',
      desk: raw.desk,
      section: raw.section,
      headline: raw.headline,
      summary: summary,
      source: source,
      image: image
    };
  }

  function validateComicSlide(raw, i) {
    if (!isNonEmptyString(raw.desk, LEN.text)) throw new Error('slide[' + i + '].desk is invalid.');
    if (!isNonEmptyString(raw.section, LEN.text)) throw new Error('slide[' + i + '].section is invalid.');
    if (!isNonEmptyString(raw.headline, LEN.headline)) throw new Error('slide[' + i + '].headline is invalid.');
    if (!Array.isArray(raw.panels) || raw.panels.length !== 3) {
      throw new Error('slide[' + i + '].panels must contain exactly 3 panels.');
    }
    var panels = [];
    for (var p = 0; p < 3; p++) {
      var panel = raw.panels[p];
      if (typeof panel !== 'object' || panel === null || Array.isArray(panel)) {
        throw new Error('slide[' + i + '].panels[' + p + '] is invalid.');
      }
      if (!isNonEmptyString(panel.title, LEN.text)) {
        throw new Error('slide[' + i + '].panels[' + p + '].title is invalid.');
      }
      if (!isNonEmptyString(panel.caption, LEN.caption)) {
        throw new Error('slide[' + i + '].panels[' + p + '].caption is invalid.');
      }
      panels.push({ title: panel.title, caption: panel.caption });
    }
    return {
      id: raw.id,
      type: 'comic',
      desk: raw.desk,
      section: raw.section,
      headline: raw.headline,
      panels: panels
    };
  }

  // ---------- Flatten into render slides (pagination) ----------

  function buildRenderSlides(model) {
    var out = [];
    for (var i = 0; i < model.slides.length; i++) {
      var s = model.slides[i];
      if (s.type === 'comic') {
        out.push({
          id: s.id,
          type: 'comic',
          desk: s.desk,
          section: s.section,
          headline: s.headline,
          panels: s.panels,
          chunkIndex: 1,
          totalChunks: 1
        });
        continue;
      }
      var words = s.summary.length ? s.summary.split(/\s+/).filter(Boolean) : [];
      var chunks = [];
      if (words.length === 0) {
        chunks.push('');
      } else {
        for (var start = 0; start < words.length; start += WORDS_PER_CHUNK) {
          chunks.push(words.slice(start, start + WORDS_PER_CHUNK).join(' '));
        }
      }
      for (var c = 0; c < chunks.length; c++) {
        out.push({
          id: s.id + (chunks.length > 1 ? ':part' + (c + 1) : ''),
          type: 'story',
          desk: s.desk,
          section: s.section,
          headline: s.headline,
          text: chunks[c],
          chunkIndex: c + 1,
          totalChunks: chunks.length,
          source: s.source,
          image: c === 0 ? s.image : null
        });
      }
    }
    return out;
  }

  // ---------- Rendering ----------

  function clearStage() {
    while (els.slideStage.firstChild) {
      els.slideStage.removeChild(els.slideStage.firstChild);
    }
  }

  function makeEl(tag, opts) {
    var el = document.createElement(tag);
    if (opts) {
      if (opts.className) el.className = opts.className;
      if (opts.text !== undefined) el.textContent = opts.text;
    }
    return el;
  }

  function renderCurrentSlide() {
    clearStage();
    var slide = state.renderSlides[state.currentIndex];
    if (!slide) return;

    els.currentSection.textContent = slide.section;
    els.slideCounter.textContent = (state.currentIndex + 1) + ' / ' + state.renderSlides.length;

    var article = makeEl('article', { className: 'slide slide-' + slide.type });

    var kickerText = slide.desk + (slide.totalChunks > 1 ? ' · Part ' + slide.chunkIndex + ' of ' + slide.totalChunks : '');
    article.appendChild(makeEl('p', { className: 'kicker', text: kickerText }));
    article.appendChild(makeEl('h2', { className: 'headline', text: slide.headline }));

    if (slide.type === 'comic') {
      var panelWrap = makeEl('div', { className: 'comic-panels' });
      for (var p = 0; p < slide.panels.length; p++) {
        var panel = slide.panels[p];
        var panelEl = makeEl('section', { className: 'comic-panel' });
        panelEl.appendChild(makeEl('h3', { text: 'Panel ' + (p + 1) + ': ' + panel.title }));
        panelEl.appendChild(makeEl('p', { text: panel.caption }));
        panelWrap.appendChild(panelEl);
      }
      article.appendChild(panelWrap);
    } else {
      var body = makeEl('div', { className: slide.image ? 'story-split' : 'story-full' });
      var textCol = makeEl('div', { className: 'story-text' });
      textCol.appendChild(makeEl('p', { className: 'summary', text: slide.text }));

      if (slide.source) {
        var srcLine = makeEl('p', { className: 'source-line' });
        var prefix = slide.source.label + (slide.source.asOf ? ' · ' + slide.source.asOf + ' · ' : ' · ');
        srcLine.appendChild(document.createTextNode(prefix));
        var a = makeEl('a', { text: 'Source' });
        a.setAttribute('href', slide.source.url);
        a.setAttribute('rel', 'noopener noreferrer');
        srcLine.appendChild(a);
        textCol.appendChild(srcLine);
      }
      body.appendChild(textCol);

      if (slide.image) {
        body.appendChild(buildImageFigure(slide.image));
      }
      article.appendChild(body);
    }

    els.slideStage.appendChild(article);
  }

  function buildImageFigure(image) {
    var figure = makeEl('figure', { className: 'photo-figure' });
    figure.appendChild(makeEl('p', {
      className: 'archive-label',
      text: image.isArchive ? 'Archive / illustrative image' : 'Illustrative image'
    }));

    var img = document.createElement('img');
    img.alt = image.alt;
    img.loading = 'lazy';
    img.decoding = 'async';
    var fallback = makeEl('p', { className: 'image-fallback', text: image.alt });
    fallback.hidden = true;
    img.addEventListener('error', function () {
      if (img.parentNode) img.parentNode.removeChild(img);
      fallback.hidden = false;
    });
    img.src = './' + image.src;
    figure.appendChild(img);
    figure.appendChild(fallback);

    var figcaption = makeEl('figcaption');
    if (image.caption) {
      figcaption.appendChild(makeEl('span', { className: 'caption-text', text: image.caption }));
    }
    var creditBits = [];
    if (image.credit) creditBits.push('Credit: ' + image.credit);
    if (image.license) creditBits.push('License: ' + image.license);
    if (creditBits.length) {
      figcaption.appendChild(document.createElement('br'));
      figcaption.appendChild(document.createTextNode(creditBits.join(' · ')));
    }
    if (image.changes) {
      figcaption.appendChild(document.createElement('br'));
      figcaption.appendChild(document.createTextNode('Modifications: ' + image.changes));
    }
    figure.appendChild(figcaption);

    var links = makeEl('p', { className: 'image-links' });
    var hasLink = false;
    if (image.sourceUrl) {
      var a1 = makeEl('a', { text: 'Image source' });
      a1.setAttribute('href', image.sourceUrl);
      a1.setAttribute('rel', 'noopener noreferrer');
      links.appendChild(a1);
      hasLink = true;
    }
    if (image.licenseUrl) {
      if (hasLink) links.appendChild(document.createTextNode(' · '));
      var a2 = makeEl('a', { text: 'License terms' });
      a2.setAttribute('href', image.licenseUrl);
      a2.setAttribute('rel', 'noopener noreferrer');
      links.appendChild(a2);
      hasLink = true;
    }
    if (hasLink) figure.appendChild(links);
    return figure;
  }

  function renderMasthead(model) {
    els.editionTitle.textContent = model.edition.title;
    els.editionDate.textContent = 'Issue dated ' + model.edition.date;
  }

  function renderWeather(model) {
    while (els.weatherStrip.firstChild) els.weatherStrip.removeChild(els.weatherStrip.firstChild);
    for (var i = 0; i < model.weather.length; i++) {
      var w = model.weather[i];
      var card = makeEl('div', { className: 'weather-card' });
      card.appendChild(makeEl('span', { className: 'weather-location', text: w.location }));
      card.appendChild(makeEl('span', { className: 'weather-summary', text: w.summary }));
      if (w.asOf) card.appendChild(makeEl('span', { className: 'weather-asof', text: 'As of ' + w.asOf }));
      els.weatherStrip.appendChild(card);
    }
  }

  function setStatus(message) {
    els.editionStatus.textContent = message;
  }

  // ---------- Timers ----------

  function clearSlideTimer() {
    if (slideTimerId !== null) {
      clearInterval(slideTimerId);
      slideTimerId = null;
    }
  }

  function armSlideTimer() {
    clearSlideTimer();
    if (state.paused) return;
    if (document.hidden) return;
    if (state.focusSuspend) return;
    if (state.renderSlides.length < 2) return;
    slideTimerId = setInterval(advanceSlide, state.secondsPerSlide * 1000);
  }

  function advanceSlide() {
    goToSlide(state.currentIndex + 1);
  }

  function goToSlide(index) {
    var n = state.renderSlides.length;
    if (n === 0) return;
    state.currentIndex = ((index % n) + n) % n;
    renderCurrentSlide();
  }

  function navigate(delta) {
    goToSlide(state.currentIndex + delta);
    armSlideTimer();
  }

  function setPaused(paused) {
    state.paused = paused;
    els.btnPause.textContent = paused ? 'Resume' : 'Pause';
    els.btnPause.setAttribute('aria-pressed', String(paused));
    armSlideTimer();
  }

  function clearRefreshTimer() {
    if (refreshTimerId !== null) {
      clearTimeout(refreshTimerId);
      refreshTimerId = null;
    }
  }

  function scheduleRefresh() {
    clearRefreshTimer();
    refreshTimerId = setTimeout(fetchAndApply, state.refreshSeconds * 1000);
  }

  // ---------- Fetch & lifecycle ----------

  function fetchContent() {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 20000);
    return fetch(CONTENT_URL, { credentials: 'omit', redirect: 'error', cache: 'no-store', signal: controller.signal }).then(function (res) {
      if (!res.ok) throw new Error('Request failed with status ' + res.status + '.');
      return res.text();
    }).then(function (text) {
      if (text.length > MAX_RESPONSE_LENGTH) throw new Error('Content response is too large.');
      var json;
      try {
        json = JSON.parse(text);
      } catch (e) {
        throw new Error('Content is not valid JSON.');
      }
      return validateContent(json);
    }).finally(function () { clearTimeout(timeout); });
  }

  function applyModel(model, isRefresh) {
    var previousId = state.loadedOnce && state.renderSlides.length
      ? state.renderSlides[state.currentIndex].id
      : null;

    var newRenderSlides = buildRenderSlides(model);

    state.renderSlides = newRenderSlides;
    state.editionId = model.edition.id;
    state.secondsPerSlide = durationOverride || model.playback.secondsPerSlide;
    els.durationSelect.value = String(state.secondsPerSlide);
    state.refreshSeconds = model.playback.refreshSeconds;

    if (previousId !== null) {
      var found = -1;
      for (var i = 0; i < newRenderSlides.length; i++) {
        if (newRenderSlides[i].id === previousId) {
          found = i;
          break;
        }
      }
      state.currentIndex = found >= 0 ? found : 0;
    } else {
      state.currentIndex = 0;
    }

    renderMasthead(model);
    renderWeather(model);
    renderCurrentSlide();

    currentEdition = model.edition;
    freshnessDeadline = Date.parse(model.edition.generatedAt) + 24 * 3600000;
    if (!isFinite(freshnessDeadline) || Date.parse(model.edition.generatedAt) > Date.now() + 900000) freshnessDeadline = 0;
    model.weather.forEach(function (w) {
      var deadline = Date.parse(w.asOf) + 24 * 3600000;
      freshnessDeadline = Math.min(freshnessDeadline, isFinite(deadline) ? deadline : 0);
    });
    model.slides.forEach(function (s) {
      if (s.source) {
        var deadline = Date.parse(s.source.asOf) + 48 * 3600000;
        freshnessDeadline = Math.min(freshnessDeadline, isFinite(deadline) ? deadline : 0);
      }
    });
    updateFailure = false;
    editionStatus();

    enableControls(true);
    state.loadedOnce = true;

    armSlideTimer();
    scheduleRefresh();
  }

  function enableControls(enabled) {
    els.btnPrev.disabled = !enabled;
    els.btnNext.disabled = !enabled;
    els.btnPause.disabled = !enabled;
    els.durationSelect.disabled = !enabled;
  }

  function showInitialError(message) {
    clearStage();
    var article = makeEl('article', { className: 'slide error-slide' });
    article.appendChild(makeEl('h2', { text: 'Unable to load edition' }));
    article.appendChild(makeEl('p', { text: message }));
    var retryBtn = document.createElement('button');
    retryBtn.type = 'button';
    retryBtn.id = 'btn-retry';
    retryBtn.textContent = 'Retry';
    retryBtn.addEventListener('click', fetchAndApply);
    article.appendChild(retryBtn);
    els.slideStage.appendChild(article);
    els.slideCounter.textContent = '0 / 0';
    els.currentSection.textContent = '';
    setStatus('Failed to load edition.');
    enableControls(false);
  }

  function fetchAndApply() {
    if (fetchInFlight) return fetchInFlight;
    clearRefreshTimer();
    setStatus(state.loadedOnce ? 'Checking for updates…' : 'Loading edition…');
    fetchInFlight = fetchContent().then(function (model) {
      applyModel(model, state.loadedOnce);
    }).catch(function (err) {
      if (state.loadedOnce) {
        updateFailure = true;
        editionStatus();
        scheduleRefresh();
      } else {
        showInitialError(err && err.message ? err.message : 'Unknown error.');
      }
    }).finally(function () { fetchInFlight = null; });
    return fetchInFlight;
  }

  // ---------- Fullscreen ----------

  function fullscreenSupported() {
    return !!(document.documentElement.requestFullscreen);
  }

  function toggleFullscreen() {
    if (!fullscreenSupported()) {
      setStatus('Fullscreen is not supported in this browser.');
      return;
    }
    if (!document.fullscreenElement) {
      els.tvFrame.requestFullscreen().catch(function () {
        setStatus('Fullscreen request was denied.');
      });
    } else {
      document.exitFullscreen().catch(function () {});
    }
  }

  // ---------- Events ----------

  function isFormControl(el) {
    if (!el) return false;
    var tag = el.tagName;
    return tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA' || el.isContentEditable;
  }

  function isNativeActivation(el) {
    if (!el) return false;
    return el.tagName === 'BUTTON' || el.tagName === 'A';
  }

  function onKeydown(e) {
    var target = e.target;
    if (isFormControl(target)) return;
    if (e.key === 'ArrowRight') {
      e.preventDefault();
      navigate(1);
    } else if (e.key === 'ArrowLeft') {
      e.preventDefault();
      navigate(-1);
    } else if (e.key === ' ' || e.code === 'Space') {
      if (isFormControl(target) || isNativeActivation(target)) return;
      e.preventDefault();
      setPaused(!state.paused);
    } else if (e.key === 'f' || e.key === 'F') {
      if (isFormControl(target)) return;
      e.preventDefault();
      toggleFullscreen();
    }
  }

  function onFocusIn(e) {
    if (e.target && e.target.tagName === 'A' && els.slideStage.contains(e.target)) {
      state.focusSuspend = true;
      armSlideTimer();
    }
  }

  function onFocusOut(e) {
    if (e.target && e.target.tagName === 'A' && els.slideStage.contains(e.target)) {
      state.focusSuspend = false;
      armSlideTimer();
    }
  }

  function onVisibilityChange() {
    if (document.hidden) {
      clearSlideTimer();
    } else {
      armSlideTimer();
      fetchAndApply();
    }
  }

  function wireEvents() {
    els.btnPrev.addEventListener('click', function () { navigate(-1); });
    els.btnNext.addEventListener('click', function () { navigate(1); });
    els.btnPause.addEventListener('click', function () { setPaused(!state.paused); });
    els.btnReload.addEventListener('click', fetchAndApply);
    els.btnFullscreen.addEventListener('click', toggleFullscreen);
    els.durationSelect.addEventListener('change', function () {
      var v = parseInt(els.durationSelect.value, 10);
      if (isFinite(v)) {
        state.secondsPerSlide = Math.min(MAX_SLIDE_SECONDS, Math.max(MIN_SLIDE_SECONDS, v));
        durationOverride = state.secondsPerSlide;
        armSlideTimer();
      }
    });
    document.addEventListener('keydown', onKeydown);
    els.slideStage.addEventListener('focusin', onFocusIn);
    els.slideStage.addEventListener('focusout', onFocusOut);
    document.addEventListener('visibilitychange', onVisibilityChange);
    if (!fullscreenSupported()) {
      els.btnFullscreen.disabled = true;
      els.btnFullscreen.title = 'Fullscreen is not supported in this browser.';
    }
  }

  function init() {
    cacheEls();
    enableControls(false);
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      state.paused = true;
      els.btnPause.textContent = 'Resume';
      els.btnPause.setAttribute('aria-pressed', 'true');
    }
    wireEvents();
    setInterval(editionStatus, 60000);
    fetchAndApply();
  }

  document.addEventListener('DOMContentLoaded', init);
})();
