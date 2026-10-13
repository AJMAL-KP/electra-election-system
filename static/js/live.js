/**
 * Electra Live Dashboard Interactive Logic (references/07_live.png)
 * Handles:
 * - Real-time countdown to scheduled cutoff time
 * - Micro-copy of technical device IDs and cleartext passwords
 * - In-place credential rotation ("Rotate pass")
 * - WebSocket subscription to ws/admin/live/ (ballot.recorded, election.closed, kiosk.presence)
 * - Fallback periodic polling and auto-cutoff expiry redirect
 * - End Election confirmation modal
 */

document.addEventListener('DOMContentLoaded', function () {
    const configEl = document.getElementById('live-config');
    if (!configEl) return;

    const electionId = configEl.getAttribute('data-election-id');
    const endsAtStr = configEl.getAttribute('data-ends-at');
    const statsUrl = configEl.getAttribute('data-stats-url');
    const resultsUrl = configEl.getAttribute('data-results-url');
    const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';

    // =========================================================================
    // 1. COUNTDOWN TIMER
    // =========================================================================
    const digitsEl = document.getElementById('countdown-digits');
    let endsTime = endsAtStr ? new Date(endsAtStr).getTime() : null;
    let countdownInterval = null;

    function updateCountdown() {
        if (!endsTime || isNaN(endsTime)) {
            if (digitsEl) digitsEl.textContent = '-- : -- : --';
            return;
        }

        const now = Date.now();
        const diff = endsTime - now;

        if (diff <= 0) {
            if (digitsEl) digitsEl.textContent = '00 : 00 : 00';
            if (countdownInterval) {
                clearInterval(countdownInterval);
                countdownInterval = null;
            }
            // Trigger server expiry check
            fetchStats();
            return;
        }

        const totalSeconds = Math.floor(diff / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;

        const pad = n => String(n).padStart(2, '0');
        if (digitsEl) {
            digitsEl.textContent = `${pad(hours)} : ${pad(minutes)} : ${pad(seconds)}`;
        }
    }

    if (endsTime) {
        updateCountdown();
        countdownInterval = setInterval(updateCountdown, 1000);
    }

    // =========================================================================
    // 2. TOAST NOTIFICATIONS
    // =========================================================================
    window.showLiveToast = function (message, type = 'success') {
        let container = document.getElementById('live-toast-container');
        if (!container) {
            container = document.createElement('div');
            container.id = 'live-toast-container';
            container.className = 'live-toast-container';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `live-toast is-${type}`;
        toast.textContent = message;
        container.appendChild(toast);

        setTimeout(() => {
            toast.classList.add('is-hiding');
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    };

    // Show initial Django messages if present
    document.querySelectorAll('#django-messages-data [data-django-message]').forEach(msg => {
        const text = msg.textContent.trim();
        const type = msg.getAttribute('data-type') || 'success';
        if (text) showLiveToast(text, type);
    });

    // =========================================================================
    // 3. COPY CREDENTIALS TO CLIPBOARD (works on HTTPS & HTTP LAN)
    // =========================================================================
    window.copyLiveText = function (text, btn) {
        if (!text) return;

        function onSuccess() {
            btn.classList.add('is-copied');
            showLiveToast(`Copied to clipboard: ${text}`, 'success');
            setTimeout(() => btn.classList.remove('is-copied'), 1500);
        }

        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(onSuccess).catch(() => {
                fallbackLiveCopy(text, onSuccess);
            });
        } else {
            fallbackLiveCopy(text, onSuccess);
        }
    };

    function fallbackLiveCopy(text, cb) {
        try {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            textarea.style.position = 'fixed';
            textarea.style.top = '-9999px';
            textarea.style.left = '-9999px';
            textarea.setAttribute('readonly', '');
            document.body.appendChild(textarea);
            textarea.select();
            const success = document.execCommand('copy');
            document.body.removeChild(textarea);
            if (success) {
                if (cb) cb();
            } else {
                showLiveToast('Failed to copy to clipboard.', 'error');
            }
        } catch (err) {
            console.error('Fallback copy error:', err);
            showLiveToast('Failed to copy to clipboard.', 'error');
        }
    }

    // =========================================================================
    // 4. IN-PLACE PASSWORD ROTATION ("Rotate pass")
    // =========================================================================
    window.rotateLivePass = function (btn) {
        const devId = btn.getAttribute('data-device-id');
        const devIdent = btn.getAttribute('data-identifier');
        if (!devId) return;

        btn.disabled = true;
        btn.classList.add('is-spinning');

        const formData = new FormData();
        formData.append('action', 'rotate_pass');
        formData.append('device_id', devId);
        formData.append('is_ajax', '1');
        formData.append('csrfmiddlewaretoken', csrfToken);

        fetch(window.location.href, {
            method: 'POST',
            body: formData,
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(res => res.json())
        .then(data => {
            btn.disabled = false;
            btn.classList.remove('is-spinning');

            if (data.status === 'success') {
                // Update password display
                const pwTextEl = document.getElementById(`pw-text-${devId}`);
                if (pwTextEl) {
                    pwTextEl.textContent = data.new_password;
                    const pillEl = pwTextEl.closest('.device-cred-pill');
                    if (pillEl) pillEl.setAttribute('title', `Password: ${data.new_password}`);
                }

                // Update copy button onclick
                const copyBtn = document.getElementById(`copy-btn-pw-${devId}`);
                if (copyBtn) {
                    copyBtn.setAttribute('onclick', `copyLiveText('${data.new_password}', this)`);
                }

                // Set status badge to Offline (existing sessions are revoked upon rotation)
                const badge = document.getElementById(`status-badge-${devId}`);
                if (badge) {
                    badge.className = 'device-status-badge is-offline';
                    const textSpan = badge.querySelector('.status-text');
                    if (textSpan) textSpan.textContent = 'Offline';
                }

                showLiveToast(`New password generated for ${data.identifier || devIdent}. Session revoked.`, 'success');
            } else {
                showLiveToast(data.message || 'Error rotating password.', 'error');
            }
        })
        .catch(err => {
            btn.disabled = false;
            btn.classList.remove('is-spinning');
            showLiveToast('Failed to rotate password. Please try again.', 'error');
        });
    };

    // =========================================================================
    // 5. STATS REFRESH (AJAX & WS CONSUMER)
    // =========================================================================
    function fetchStats(highlightBoothId = null) {
        if (!statsUrl) return;

        fetch(statsUrl, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' }
        })
        .then(res => res.json())
        .then(data => {
            if (data.is_closed) {
                showLiveToast('Election has closed. Redirecting to official results...', 'success');
                setTimeout(() => {
                    window.location.href = data.redirect_url || resultsUrl;
                }, 1000);
                return;
            }

            // Update top metrics
            const votersEl = document.getElementById('metric-total-voters');
            const votedEl = document.getElementById('metric-total-voted');
            const pctEl = document.getElementById('metric-turnout-pct');
            const fillEl = document.getElementById('metric-progress-fill');
            const progPctEl = document.getElementById('metric-progress-pct');

            if (votersEl) votersEl.textContent = data.total_voters.toLocaleString();
            if (votedEl) votedEl.textContent = data.total_voted.toLocaleString();
            if (pctEl) pctEl.textContent = `${data.turnout_pct}%`;
            if (fillEl) fillEl.style.width = `${data.turnout_pct}%`;
            if (progPctEl) progPctEl.textContent = `${data.turnout_pct}%`;

            // Update booth metrics & device statuses
            if (data.booth_stats && Array.isArray(data.booth_stats)) {
                data.booth_stats.forEach(b => {
                    const ratioEl = document.getElementById(`booth-ratio-${b.booth_id}`);
                    const pctSpan = document.getElementById(`booth-pct-${b.booth_id}`);
                    if (ratioEl) ratioEl.textContent = `${b.voted_count} / ${b.allocated_count}`;
                    if (pctSpan) pctSpan.textContent = `${b.turnout_pct}%`;

                    // Update officer status
                    if (b.officer_device_id) {
                        const offBadge = document.getElementById(`status-badge-${b.officer_device_id}`);
                        if (offBadge) {
                            offBadge.className = `device-status-badge ${b.officer_online ? 'is-online' : 'is-offline'}`;
                            const txt = offBadge.querySelector('.status-text');
                            if (txt) txt.textContent = b.officer_online ? 'Online' : 'Offline';
                        }
                    }

                    // Update kiosk status
                    if (b.kiosk_device_id) {
                        const kioskBadge = document.getElementById(`status-badge-${b.kiosk_device_id}`);
                        if (kioskBadge) {
                            kioskBadge.className = `device-status-badge ${b.kiosk_online ? 'is-online' : 'is-offline'}`;
                            const txt = kioskBadge.querySelector('.status-text');
                            if (txt) txt.textContent = b.kiosk_online ? 'Online' : 'Offline';
                        }
                    }

                    // Visual highlight if specified
                    if (highlightBoothId && String(b.booth_id) === String(highlightBoothId)) {
                        const card = document.getElementById(`booth-card-${b.booth_id}`);
                        if (card) {
                            card.classList.add('is-updated');
                            setTimeout(() => card.classList.remove('is-updated'), 1800);
                        }
                    }
                });
            }
        })
        .catch(err => {
            console.error('Failed to fetch live stats', err);
        });
    }

    // =========================================================================
    // 6. WEBSOCKET CONNECTION (REAL-TIME UPDATES)
    // =========================================================================
    let ws = null;
    function initWebSocket() {
        const protocol = window.location.protocol === 'https:' ? 'wss://' : 'ws://';
        const wsUrl = `${protocol}${window.location.host}/ws/admin/live/`;

        try {
            ws = new WebSocket(wsUrl);

            ws.onmessage = function (e) {
                try {
                    const data = JSON.parse(e.data);
                    if (data.event === 'ballot.recorded') {
                        fetchStats(data.booth_id);
                    } else if (data.event === 'kiosk.presence') {
                        fetchStats(data.booth_id);
                    } else if (data.event === 'election.closed') {
                        showLiveToast('Election has closed. Redirecting to official results...', 'success');
                        setTimeout(() => {
                            window.location.href = resultsUrl;
                        }, 1200);
                    }
                } catch (parseErr) {
                    console.error('Error handling WebSocket message', parseErr);
                }
            };

            ws.onclose = function () {
                // Attempt reconnect after 3 seconds
                setTimeout(initWebSocket, 3000);
            };
        } catch (err) {
            console.warn('WebSocket connection unavailable, using auto-poll fallback.');
        }
    }

    initWebSocket();

    // Fallback periodic poll every 5 seconds for cutoff checks and connection lag
    setInterval(() => {
        fetchStats();
    }, 5000);

    // =========================================================================
    // 7. END ELECTION MODAL
    // =========================================================================
    const btnTriggerEnd = document.getElementById('btn-trigger-end-election');
    const modalEnd = document.getElementById('modal-end-election');
    const backdropEnd = document.getElementById('end-modal-backdrop');
    const btnCloseEnd = document.getElementById('btn-close-end-modal');
    const btnCancelEnd = document.getElementById('btn-cancel-end-modal');

    function openEndModal() {
        if (modalEnd) {
            modalEnd.classList.add('active');
            modalEnd.setAttribute('aria-hidden', 'false');
        }
    }

    function closeEndModal() {
        if (modalEnd) {
            modalEnd.classList.remove('active');
            modalEnd.setAttribute('aria-hidden', 'true');
        }
    }

    if (btnTriggerEnd) btnTriggerEnd.addEventListener('click', openEndModal);
    if (backdropEnd) backdropEnd.addEventListener('click', closeEndModal);
    if (btnCloseEnd) btnCloseEnd.addEventListener('click', closeEndModal);
    if (btnCancelEnd) btnCancelEnd.addEventListener('click', closeEndModal);

    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && modalEnd && modalEnd.classList.contains('active')) {
            closeEndModal();
        }
    });
});
