"use strict";
window.CompanionMedia = (() => {
  const validId = value => typeof value === 'string' && /^[a-z0-9][a-z0-9-]{0,79}$/.test(value);
  function resource(value, id, kind, origin) {
    if (!validId(id) || typeof value !== 'string') return null;
    try {
      const url = new URL(value, origin);
      return url.origin === origin && url.pathname === `/api/media/${id}/${kind}` && !url.hash ? url.pathname + url.search : null;
    } catch (_) { return null; }
  }
  function duration(seconds) {
    if (!Number.isFinite(seconds) || seconds < 0) return '';
    const total = Math.round(seconds), minutes = Math.floor(total / 60);
    return `${minutes}:${String(total % 60).padStart(2, '0')}`;
  }
  function fileSize(bytes) {
    if (!Number.isFinite(bytes) || bytes < 0) return '';
    return bytes >= 1024 * 1024 ? `${(bytes / (1024 * 1024)).toFixed(1)} MB` : `${Math.max(1, Math.ceil(bytes / 1024))} KB`;
  }
  function install() {
    const el = id => document.getElementById(id), video = el('review-video');
    let items = [], selected = null, requestVersion = 0;
    const requested = new URLSearchParams(location.search).get('item');
    let desired = validId(requested) ? requested : null;
    function pairLink() { el('pair-link').href = desired ? '/?media=' + encodeURIComponent(desired) : '/'; }
    function notice(text, state = '', retry = false, pair = false) {
      const node = el('media-notice'); node.hidden = false; node.dataset.state = state;
      el('notice-text').textContent = text; el('retry-library').hidden = !retry; el('pair-link').hidden = !pair; pairLink();
    }
    function clearPlayer() {
      selected = null; video.pause(); video.removeAttribute('src'); video.removeAttribute('poster');
      video.querySelectorAll('track').forEach(track => track.remove()); video.load();
    }
    function lock() {
      clearPlayer(); items = []; el('media-library').replaceChildren(); el('media-workspace').hidden = true;
      document.title = 'Media review · Desktop Companion'; el('media-title').textContent = ''; el('media-description').textContent = ''; el('media-facts').replaceChildren();
      el('download-media').removeAttribute('href'); el('download-captions').removeAttribute('href');
      notice('Pair this browser with your Mac to watch your private media. If you were already paired, your session may have expired.', '', false, true);
    }
    function status(text, error = false) {
      el('playback-status').textContent = text; el('playback-status').dataset.error = String(error); el('retry-playback').hidden = !error;
    }
    function renderLibrary() {
      el('media-library').replaceChildren(); el('library-count').textContent = String(items.length);
      for (const item of items) {
        const button = document.createElement('button'); button.type = 'button'; button.className = 'media-library-item'; button.setAttribute('aria-current', String(item.id === selected?.id));
        const title = document.createElement('strong'); title.textContent = item.title || 'Untitled media';
        const detail = document.createElement('small'); detail.textContent = [item.version, duration(item.duration_seconds)].filter(Boolean).join(' · ');
        button.append(title, detail); button.addEventListener('click', () => choose(item)); el('media-library').append(button);
      }
    }
    function choose(item, reload = false) {
      if (!reload && selected?.id === item.id) return;
      clearPlayer(); selected = item; desired = item.id; pairLink();
      const url = new URL(location.href); url.searchParams.set('item', item.id); history.replaceState(null, '', url.pathname + url.search);
      video.src = item.video_url; if (item.poster_url) video.poster = item.poster_url;
      if (item.captions_url) {
        const track = document.createElement('track'); track.kind = 'captions'; track.label = 'English'; track.srclang = 'en'; track.src = item.captions_url; video.append(track);
      }
      el('media-title').textContent = item.title || 'Untitled media'; document.title = (item.title || 'Media review') + ' · Desktop Companion';
      el('media-description').textContent = item.description || ''; el('media-description').hidden = !item.description;
      el('media-facts').replaceChildren();
      for (const fact of [item.version, duration(item.duration_seconds), fileSize(item.bytes)]) if (fact) { const span = document.createElement('span'); span.textContent = fact; el('media-facts').append(span); }
      el('download-media').href = item.download_url;
      el('download-captions').hidden = !item.captions_url;
      if (item.captions_url) el('download-captions').href = item.captions_url; else el('download-captions').removeAttribute('href');
      status('Tap play when you’re ready.'); renderLibrary();
    }
    function sanitize(item) {
      if (!item || !validId(item.id)) return null;
      const origin = location.origin, video_url = resource(item.video_url, item.id, 'video', origin), download_url = resource(item.download_url, item.id, 'download', origin);
      if (!video_url || !download_url) return null;
      return {id:item.id, title:typeof item.title === 'string' ? item.title : '', description:typeof item.description === 'string' ? item.description : '', version:typeof item.version === 'string' ? item.version : '', duration_seconds:item.duration_seconds, bytes:item.bytes, video_url, download_url, poster_url:resource(item.poster_url, item.id, 'poster', origin), captions_url:resource(item.captions_url, item.id, 'captions', origin)};
    }
    async function loadLibrary() {
      const version = ++requestVersion; el('library-refresh').disabled = true; el('retry-library').hidden = true;
      if (!items.length) notice('Loading your private library…');
      try {
        const response = await fetch('/api/media', {cache:'no-store', credentials:'same-origin'});
        if (version !== requestVersion) return;
        if (response.status === 401) return lock();
        if (!response.ok) throw new Error(response.status === 403 ? 'This address is not enabled for your browser. Open the usual Companion address, then try Media review again.' : 'Your Mac could not load the media library. Check its connection and try again.');
        const data = await response.json(); if (version !== requestVersion) return;
        if (!Array.isArray(data.items)) throw new Error('The media library returned an unexpected response. Refresh the page and try again.');
        items = data.items.map(sanitize).filter(Boolean);
        if (!items.length) { clearPlayer(); el('media-workspace').hidden = true; return notice('No media is available yet. Add a finished cut to the private library on your Mac, then refresh.'); }
        el('media-workspace').hidden = false; el('media-notice').hidden = true;
        const item = items.find(item => item.id === desired) || items[0];
        const missing = desired && !items.some(item => item.id === desired);
        if (selected?.id !== item.id || selected.video_url !== item.video_url) choose(item); else renderLibrary();
        if (missing) notice('That cut is no longer in this library. Showing another available cut.');
      } catch (error) {
        if (version !== requestVersion) return;
        notice(error instanceof TypeError ? 'Could not reach your Mac. Check your connection, then try again.' : error.message, 'error', true);
      } finally { if (version === requestVersion) el('library-refresh').disabled = false; }
    }
    video.addEventListener('loadedmetadata', () => { if (selected) status('Ready to review. Tap play.'); });
    video.addEventListener('playing', () => { if (selected) status('Playing · use the controls to seek or change speed.'); });
    video.addEventListener('waiting', () => { if (selected && !video.paused) status('Buffering… playback will resume when more video arrives.'); });
    video.addEventListener('ended', () => { if (selected) status('Review complete. Replay or download this cut.'); });
    video.addEventListener('error', async () => {
      if (!selected || !video.error || video.error.code === 1) return;
      const id = selected.id;
      try { const response = await fetch('/api/auth/status', {cache:'no-store', credentials:'same-origin'}); const auth = response.ok ? await response.json() : null; if (selected?.id !== id) return; if (auth && !auth.authenticated) return lock(); } catch (_) {}
      if (selected?.id === id) status(video.error?.code === 3 || video.error?.code === 4 ? 'This browser could not play the video. Try reloading the player, another browser, or the download below.' : 'Streaming was interrupted. Check your connection and that your Mac is awake, then reload the player.', true);
    });
    el('retry-playback').addEventListener('click', () => { if (selected) choose(selected, true); });
    el('library-refresh').addEventListener('click', loadLibrary); el('retry-library').addEventListener('click', loadLibrary);
    document.addEventListener('visibilitychange', () => { if (!document.hidden && !selected) loadLibrary(); });
    pairLink(); loadLibrary();
  }
  if (typeof document !== 'undefined') document.addEventListener('DOMContentLoaded', install, {once:true});
  return {validId, resource, duration, fileSize};
})();
