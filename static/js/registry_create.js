/**
 * Electra - Initial Voter Registry Setup with File Import
 * Transitions smoothly from State 1 (half-screen file dropzone)
 * to State 2 (04_voter_registry_initial.png configuration & live preview).
 */

document.addEventListener('DOMContentLoaded', function () {
    const uploadScreen = document.getElementById('state-upload-screen');
    const configScreen = document.getElementById('state-config-screen');
    const dropzoneBox = document.getElementById('upload-dropzone-box');
    const fileInput = document.getElementById('file-input');
    const uploadLoading = document.getElementById('upload-loading-overlay');
    const uploadPrompt = document.getElementById('upload-prompt-container');
    const customAlert = document.getElementById('custom-alert');
    const form = document.getElementById('registry-create-form');

    const regNameInput = document.getElementById('reg-name');
    const regDescInput = document.getElementById('reg-desc');

    const selectPrimaryId = document.getElementById('col-primary-id');
    const selectName = document.getElementById('col-name');
    const selectGroup = document.getElementById('col-group');
    const selectSubgroup = document.getElementById('col-subgroup');
    const selectGender = document.getElementById('col-gender');

    const previewThead = document.getElementById('preview-thead');
    const previewTbody = document.getElementById('preview-tbody');

    const displayFilename = document.getElementById('display-filename');
    const displayFilemeta = document.getElementById('display-filemeta');

    // Server-provided pending data if present on initial load
    const initialDataEl = document.getElementById('initial-parse-data');
    let parsedHeaders = [];
    let parsedRows = [];
    let detectedMapping = {};

    function showAlert(msg, isSuccess = false) {
        if (typeof window.showElectraToast === 'function') {
            window.showElectraToast(msg, isSuccess ? 'success' : 'error', 5000);
        } else if (customAlert) {
            customAlert.textContent = msg;
            customAlert.className = 'custom-alert-box ' + (isSuccess ? 'custom-alert-success' : 'custom-alert-error');
            customAlert.style.display = 'flex';
        }
    }

    function clearAlert() {
        if (customAlert) {
            customAlert.style.display = 'none';
            customAlert.textContent = '';
        }
    }

    function cleanFilenameToTitle(filename) {
        if (!filename) return '';
        const nameWithoutExt = filename.replace(/\.[^/.]+$/, '');
        return nameWithoutExt
            .replace(/[_-]/g, ' ')
            .replace(/\b\w/g, char => char.toUpperCase())
            .trim();
    }

    function populateSelectOptions(selectEl, headers, selectedValue, includeNone = false) {
        if (!selectEl) return;
        selectEl.innerHTML = '';
        if (includeNone) {
            const optNone = document.createElement('option');
            optNone.value = '';
            optNone.textContent = '(None - Optional)';
            if (!selectedValue || selectedValue.toLowerCase() === '(none)' || selectedValue.toLowerCase() === '(none - optional)') {
                optNone.selected = true;
            }
            selectEl.appendChild(optNone);
        }

        headers.forEach(h => {
            const opt = document.createElement('option');
            opt.value = h;
            opt.textContent = h;
            if (selectedValue && (h.toLowerCase() === selectedValue.toLowerCase() || h === selectedValue)) {
                opt.selected = true;
            }
            selectEl.appendChild(opt);
        });

        // If nothing explicitly matched and not includeNone, select first option
        if (!includeNone && !selectEl.value && selectEl.options.length > 0) {
            selectEl.selectedIndex = 0;
        }
    }

    const toggleSubgroupYes = document.getElementById('toggle-subgroup-yes');
    const toggleSubgroupNo = document.getElementById('toggle-subgroup-no');
    const hasSubgroupsToggleInput = document.getElementById('has-subgroups-toggle');
    const subgroupSelectWrapper = document.getElementById('subgroup-select-wrapper');
    const subgroupOffHint = document.getElementById('subgroup-off-hint');
    const btnChangeFile = document.getElementById('btn-change-file');

    function setSubgroupToggle(enabled) {
        if (enabled) {
            if (toggleSubgroupYes) toggleSubgroupYes.classList.add('active');
            if (toggleSubgroupNo) toggleSubgroupNo.classList.remove('active');
            if (hasSubgroupsToggleInput) hasSubgroupsToggleInput.value = 'yes';
            if (subgroupSelectWrapper) subgroupSelectWrapper.style.display = 'block';
            if (subgroupOffHint) subgroupOffHint.style.display = 'none';
            if (selectSubgroup) {
                selectSubgroup.disabled = false;
                if (!selectSubgroup.value && selectSubgroup.options.length > 0) {
                    selectSubgroup.selectedIndex = 0;
                }
            }
        } else {
            if (toggleSubgroupYes) toggleSubgroupYes.classList.remove('active');
            if (toggleSubgroupNo) toggleSubgroupNo.classList.add('active');
            if (hasSubgroupsToggleInput) hasSubgroupsToggleInput.value = 'no';
            if (subgroupSelectWrapper) subgroupSelectWrapper.style.display = 'none';
            if (subgroupOffHint) subgroupOffHint.style.display = 'flex';
            if (selectSubgroup) {
                selectSubgroup.value = '';
            }
        }
        renderPreviewTable();
    }

    if (toggleSubgroupYes) {
        toggleSubgroupYes.addEventListener('click', function () {
            setSubgroupToggle(true);
        });
    }

    if (toggleSubgroupNo) {
        toggleSubgroupNo.addEventListener('click', function () {
            setSubgroupToggle(false);
        });
    }

    if (btnChangeFile) {
        btnChangeFile.addEventListener('click', function () {
            configScreen.style.display = 'none';
            uploadScreen.style.display = 'flex';
            const errBanner = document.getElementById('config-error-banner');
            if (errBanner) errBanner.style.display = 'none';
            if (fileInput) fileInput.value = '';
        });
    }

    function renderPreviewTable() {
        if (!previewThead || !previewTbody) return;

        const isSubgroupActive = hasSubgroupsToggleInput
            ? (hasSubgroupsToggleInput.value === 'yes')
            : Boolean(selectSubgroup && selectSubgroup.value);

        const mapping = [
            { field: 'Student ID', colName: selectPrimaryId ? selectPrimaryId.value : '' },
            { field: 'Full Name', colName: selectName ? selectName.value : '' },
            { field: 'Department', colName: selectGroup ? selectGroup.value : '' },
        ];
        if (isSubgroupActive && selectSubgroup && selectSubgroup.value) {
            mapping.push({ field: 'Semester', colName: selectSubgroup.value });
        }
        if (selectGender) {
            mapping.push({ field: 'Gender', colName: selectGender.value });
        }

        // 1. Build Header
        let thHtml = '<tr>';
        mapping.forEach(m => {
            const displayTitle = m.colName ? m.colName.toUpperCase() : m.field.toUpperCase();
            thHtml += `<th>${escapeHtml(displayTitle)}</th>`;
        });
        thHtml += '</tr>';
        previewThead.innerHTML = thHtml;

        // 2. Build Rows (up to 5 rows)
        let tbHtml = '';
        const rowsToRender = parsedRows.slice(0, 5);

        if (rowsToRender.length === 0) {
            tbHtml = `<tr><td colspan="${mapping.length}" class="preview-empty-cell" style="text-align: center; padding: 24px;">No rows available for preview.</td></tr>`;
        } else {
            rowsToRender.forEach(row => {
                tbHtml += '<tr>';
                mapping.forEach(m => {
                    let cellVal = '—';
                    if (m.colName) {
                        const colIdx = parsedHeaders.indexOf(m.colName);
                        if (colIdx !== -1 && colIdx < row.length && row[colIdx] !== null && row[colIdx] !== undefined) {
                            cellVal = String(row[colIdx]).trim() || '—';
                        }
                    }
                    tbHtml += `<td>${escapeHtml(cellVal)}</td>`;
                });
                tbHtml += '</tr>';
            });
        }
        previewTbody.innerHTML = tbHtml;
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    function setupState2(data) {
        parsedHeaders = data.headers || [];
        parsedRows = data.preview_rows || data.data_rows || [];
        detectedMapping = data.detected || {};

        if (displayFilename) {
            displayFilename.textContent = data.filename || 'Uploaded file';
        }
        if (displayFilemeta) {
            const total = data.total_rows || (data.data_rows ? data.data_rows.length : (data.preview_rows ? data.preview_rows.length : 0));
            displayFilemeta.textContent = `${total.toLocaleString()} records · ${data.filesize || 'Uploaded'}`;
        }

        if (data.filename && (!regNameInput.value || regNameInput.value.trim() === '')) {
            regNameInput.value = cleanFilenameToTitle(data.filename);
        }

        populateSelectOptions(selectPrimaryId, parsedHeaders, detectedMapping.primary_id);
        populateSelectOptions(selectName, parsedHeaders, detectedMapping.name);
        populateSelectOptions(selectGroup, parsedHeaders, detectedMapping.group);
        populateSelectOptions(selectSubgroup, parsedHeaders, detectedMapping.subgroup, true);
        populateSelectOptions(selectGender, parsedHeaders, detectedMapping.gender);

        // Subgroup toggle initialization
        let enableSub = true;
        if (data.has_subgroups_toggle === 'no') {
            enableSub = false;
        } else if (data.has_subgroups_toggle === 'yes') {
            enableSub = true;
        } else {
            // Auto-detect based on detected mapping
            enableSub = Boolean(detectedMapping.subgroup && detectedMapping.subgroup.trim());
        }
        setSubgroupToggle(enableSub);

        // Bind change events to live update preview
        [selectPrimaryId, selectName, selectGroup, selectSubgroup, selectGender].forEach(sel => {
            if (sel) {
                sel.removeEventListener('change', renderPreviewTable);
                sel.addEventListener('change', renderPreviewTable);
            }
        });

        // Smooth transition
        uploadScreen.style.display = 'none';
        configScreen.style.display = 'block';
    }

    function handleFileUpload(file) {
        if (!file) return;

        const fname = file.name.toLowerCase();
        if (!fname.endsWith('.csv') && !fname.endsWith('.xlsx') && !fname.endsWith('.xls')) {
            showAlert('Please select a valid CSV or Excel (.xlsx, .xls) file.');
            return;
        }

        clearAlert();
        if (uploadPrompt) uploadPrompt.style.display = 'none';
        if (uploadLoading) uploadLoading.style.display = 'flex';

        const formData = new FormData();
        formData.append('file', file);

        const csrfTokenEl = document.querySelector('[name=csrfmiddlewaretoken]');
        const csrfToken = csrfTokenEl ? csrfTokenEl.value : '';

        fetch('/voters/import/parse/', {
            method: 'POST',
            body: formData,
            headers: {
                'X-CSRFToken': csrfToken,
            },
        })
        .then(res => res.json())
        .then(data => {
            if (uploadPrompt) uploadPrompt.style.display = 'flex';
            if (uploadLoading) uploadLoading.style.display = 'none';

            if (!data.success) {
                showAlert(data.error || 'Failed to read file.');
                return;
            }

            setupState2(data);
        })
        .catch(err => {
            if (uploadPrompt) uploadPrompt.style.display = 'flex';
            if (uploadLoading) uploadLoading.style.display = 'none';
            showAlert('Network error while processing file: ' + err.message);
        });
    }

    // Dropzone event listeners
    if (dropzoneBox && fileInput) {
        dropzoneBox.addEventListener('click', () => fileInput.click());

        fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                handleFileUpload(e.target.files[0]);
            }
        });

        ['dragenter', 'dragover'].forEach(eventName => {
            dropzoneBox.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzoneBox.classList.add('drag-over');
            });
        });

        ['dragleave', 'drop'].forEach(eventName => {
            dropzoneBox.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
                dropzoneBox.classList.remove('drag-over');
            });
        });

        dropzoneBox.addEventListener('drop', (e) => {
            const dt = e.dataTransfer;
            if (dt && dt.files && dt.files.length > 0) {
                handleFileUpload(dt.files[0]);
            }
        });
    }

    // Form submission validation
    if (form) {
        form.addEventListener('submit', function (e) {
            const nameVal = regNameInput.value.trim();
            const idCol = selectPrimaryId.value;
            const nameCol = selectName.value;

            if (!nameVal) {
                e.preventDefault();
                showAlert('Please provide a name for this registry.');
                regNameInput.focus();
                return;
            }

            if (!idCol) {
                e.preventDefault();
                showAlert('Please select the column corresponding to Voter ID.');
                selectPrimaryId.focus();
                return;
            }

            if (!nameCol) {
                e.preventDefault();
                showAlert('Please select the column corresponding to Name.');
                selectName.focus();
                return;
            }

            const groupCol = selectGroup ? selectGroup.value : '';
            if (!groupCol) {
                e.preventDefault();
                showAlert('Please select the column corresponding to Department / Group.');
                selectGroup.focus();
                return;
            }

            // If subgroup is toggled off, ensure empty value is sent
            if (hasSubgroupsToggleInput && hasSubgroupsToggleInput.value === 'no') {
                if (selectSubgroup) {
                    selectSubgroup.disabled = false;
                    selectSubgroup.value = '';
                }
            }
        });
    }

    // Check if initial pending file data was supplied by Django context
    if (initialDataEl) {
        try {
            const initData = JSON.parse(initialDataEl.textContent);
            if (initData && initData.headers && initData.headers.length > 0) {
                setupState2(initData);
            }
        } catch (e) {
            console.error('Error parsing initial registry data:', e);
        }
    }
});
