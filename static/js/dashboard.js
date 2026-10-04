/**
 * Electra System — Dashboard Workspace UI Interactions
 * Loaded exclusively by templates/elections/dashboard.html
 */

// Filter Candidate / Contender table rows based on filter pill selection
function filterCandidateTable(filterValue, btnEl) {
    const pills = document.querySelectorAll('.filter-pill');
    pills.forEach(p => p.classList.remove('active'));
    if (btnEl) btnEl.classList.add('active');

    const rows = document.querySelectorAll('.candidate-data-row');
    rows.forEach(row => {
        if (!filterValue || filterValue === 'all') {
            row.style.display = '';
        } else {
            const pos = (row.getAttribute('data-position') || '').toLowerCase();
            const tag = (row.getAttribute('data-tag') || '').toLowerCase();
            const filterLower = filterValue.toLowerCase();
            if (pos.includes(filterLower) || tag === filterLower) {
                row.style.display = '';
            } else {
                row.style.display = 'none';
            }
        }
    });
}

// Position-specific Candidate registration modal trigger
function openAddCandidateForPosition(posId, posName) {
    const form = document.getElementById('candidate-create-form');
    const display = document.getElementById('target-position-display');
    if (form && display) {
        if (posId) {
            form.action = `/elections/positions/${posId}/candidates/create/`;
        }
        if (posName) {
            display.textContent = posName;
        }
    }
    openModal('add-candidate-modal');
}

// Prefill candidate name and academic cohort when an enrolled voter is selected
function handleCandidateVoterSelect(selectEl) {
    const opt = selectEl.options[selectEl.selectedIndex];
    if (opt && opt.value) {
        const name = opt.getAttribute('data-name');
        const group = opt.getAttribute('data-group');
        const nameInput = document.getElementById('cand_name');
        const groupInput = document.getElementById('cand_academic_group');
        if (name && nameInput) {
            nameInput.value = name;
        }
        if (group && groupInput) {
            groupInput.value = group;
        }
    }
}

// Quick suggestions helper for ballot position names
function applyPresetPosition(name) {
    const input = document.getElementById('pos_name');
    if (input) {
        input.value = name;
        input.focus();
    }
}

// Live closing countdown timer (active when live polling is in progress)
function initDashboardCountdown(isoDateString) {
    if (!isoDateString) return;
    const targetDate = new Date(isoDateString);
    if (isNaN(targetDate.getTime())) return;

    function updateCountdown() {
        const now = new Date();
        const diff = targetDate - now;

        const daysEl = document.getElementById('timer-days');
        const hoursEl = document.getElementById('timer-hours');
        const minutesEl = document.getElementById('timer-minutes');
        const secondsEl = document.getElementById('timer-seconds');

        if (!daysEl || !hoursEl || !minutesEl || !secondsEl) return;

        if (diff <= 0) {
            daysEl.textContent = "00";
            hoursEl.textContent = "00";
            minutesEl.textContent = "00";
            secondsEl.textContent = "00";
            return;
        }

        const days = Math.floor(diff / (1000 * 60 * 60 * 24));
        const hours = Math.floor((diff % (1000 * 60 * 60 * 24)) / (1000 * 60 * 60));
        const minutes = Math.floor((diff % (1000 * 60 * 60)) / (1000 * 60));
        const seconds = Math.floor((diff % (1000 * 60)) / 1000);

        daysEl.textContent = String(days).padStart(2, '0');
        hoursEl.textContent = String(hours).padStart(2, '0');
        minutesEl.textContent = String(minutes).padStart(2, '0');
        secondsEl.textContent = String(seconds).padStart(2, '0');
    }

    updateCountdown();
    setInterval(updateCountdown, 1000);
}
