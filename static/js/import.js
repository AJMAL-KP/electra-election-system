/**
 * Electra - Subsequent Voter Import
 * Faithfully implements the workflow in references/04_voter_import.png:
 * 1. State 1: Centered half-screen file dropzone.
 * 2. Smooth transition to State 2 (04_voter_import.png):
 *    - Uploaded file card with filename, record count, and "Replace file".
 *    - Missing fields detection (Group is required if missing; Sub-group is optional).
 *    - Live 5-row preview table with applied values.
 *    - Validations preventing submission without required group value.
 */

document.addEventListener('DOMContentLoaded', function () {
    // State 1 elements
    const uploadScreen = document.getElementById('state-upload-screen');
    const dropzoneBox = document.getElementById('upload-dropzone-box');
    const state1FileInput = document.getElementById('state1-file-input');
    const uploadPrompt = document.getElementById('upload-prompt-container');
    const uploadLoading = document.getElementById('upload-loading-overlay');
    const uploadErrorBox = document.getElementById('upload-error-box');
    const uploadErrorMessage = document.getElementById('upload-error-message');
    const uploadGroupPrompt = document.getElementById('upload-group-prompt');
    const uploadGroupInput = document.getElementById('upload-group-input');
    const uploadGroupContinueBtn = document.getElementById('upload-group-continue-btn');

    // State 2 elements
    const importScreen = document.getElementById('state-import-screen');
    const form = document.getElementById('voter-import-form');
    const fileInput = document.getElementById('file-input');
    const fileCardBox = document.getElementById('file-card-box');
    const displayFilename = document.getElementById('display-filename');
    const displayFilemeta = document.getElementById('display-filemeta');
    const btnReplaceFile = document.getElementById('btn-replace-file');

    const missingSection = document.getElementById('missing-fields-section');
    const missingGroupRow = document.getElementById('missing-group-row');
    const inputDefaultGroup = document.getElementById('default-group-input');
    const groupSourcePill = document.getElementById('group-source-pill');
    const missingGroupNotice = document.getElementById('missing-group-notice');

    const previewSection = document.getElementById('preview-section');
    const previewBody = document.getElementById('preview-body');
    const headerGroupApplied = document.getElementById('header-group-applied');
    const actionsStrip = document.getElementById('import-actions-strip');
    const alertBox = document.getElementById('import-custom-alert');

    const registrySchemaEl = document.getElementById('registry-schema-data');
    let registrySchema = {};
    if (registrySchemaEl) {
        try {
            registrySchema = JSON.parse(registrySchemaEl.textContent);
        } catch (e) {
            console.error('Failed to parse registry schema', e);
        }
    }

    let currentParsedData = null;

    function showAlert(msg, isError = true) {
        if (typeof window.showElectraToast === 'function') {
            window.showElectraToast(msg, isError ? 'error' : 'success', 5000);
        } else if (alertBox) {
            alertBox.textContent = msg;
            alertBox.className = 'import-alert-box ' + (isError ? 'import-alert-error' : 'import-alert-success');
            alertBox.style.display = 'block';
        }
    }

    function clearAlert() {
        if (alertBox) {
            alertBox.style.display = 'none';
            alertBox.textContent = '';
        }
    }

    function clearUploadError() {
        if (uploadErrorBox) {
            uploadErrorBox.style.display = 'none';
        }
        if (uploadErrorMessage) {
            uploadErrorMessage.textContent = '';
        }
    }

    function showUploadError(msg) {
        showAlert(msg, true);
        if (uploadErrorBox && uploadErrorMessage) {
            uploadErrorMessage.textContent = msg;
            uploadErrorBox.style.display = 'flex';
        }
        if (uploadGroupPrompt) {
            uploadGroupPrompt.style.display = 'none';
        }
        if (uploadScreen) uploadScreen.style.display = 'flex';
        if (importScreen) importScreen.style.display = 'none';
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function populateDatalist(listId, items) {
        const listEl = document.getElementById(listId);
        if (!listEl) return;
        listEl.innerHTML = '';
        (items || []).forEach(item => {
            const opt = document.createElement('option');
            opt.value = item;
            listEl.appendChild(opt);
        });
    }

    // Bind file selection on State 1 dropzone
    if (dropzoneBox && state1FileInput) {
        dropzoneBox.addEventListener('click', () => state1FileInput.click());

        state1FileInput.addEventListener('change', (e) => {
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

    // Replace file button in State 2
    if (btnReplaceFile && fileInput) {
        btnReplaceFile.addEventListener('click', () => fileInput.click());
    }

    if (fileInput) {
        fileInput.addEventListener('change', (e) => {
            if (e.target.files && e.target.files.length > 0) {
                handleFileUpload(e.target.files[0]);
            }
        });
    }

    // Handle missing group prompt continuation
    function proceedFromGroupPrompt() {
        if (!uploadGroupInput) return;
        const grpVal = uploadGroupInput.value.trim();
        if (!grpVal) {
            const grpLabel = registrySchema.group_source || 'Department';
            showAlert(`Please enter or select a ${grpLabel} name before proceeding.`);
            uploadGroupInput.focus();
            return;
        }

        if (inputDefaultGroup) {
            inputDefaultGroup.value = grpVal;
        }
        if (uploadGroupPrompt) {
            uploadGroupPrompt.style.display = 'none';
        }
        if (uploadScreen) uploadScreen.style.display = 'none';
        if (importScreen) importScreen.style.display = 'block';

        if (currentParsedData) {
            renderImportState2(currentParsedData);
        }
    }

    if (uploadGroupContinueBtn) {
        uploadGroupContinueBtn.addEventListener('click', proceedFromGroupPrompt);
    }
    if (uploadGroupInput) {
        uploadGroupInput.addEventListener('keydown', function (e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                proceedFromGroupPrompt();
            }
        });
    }

    function handleFileUpload(file) {
        if (!file) return;

        const fname = file.name.toLowerCase();
        if (!fname.endsWith('.csv') && !fname.endsWith('.xlsx') && !fname.endsWith('.xls')) {
            showUploadError('Please select a valid CSV (.csv) or Excel (.xlsx, .xls) file.');
            return;
        }

        clearAlert();
        clearUploadError();
        if (uploadPrompt) uploadPrompt.style.display = 'none';
        if (uploadLoading) uploadLoading.style.display = 'flex';

        const formData = new FormData();
        formData.append('file', file);
        if (registrySchema && registrySchema.id) {
            formData.append('registry_id', registrySchema.id);
        }

        const csrfToken = document.querySelector('[name=csrfmiddlewaretoken]')?.value || '';

        fetch('/voters/import/parse/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': csrfToken,
            },
            body: formData,
        })
        .then(res => res.json())
        .then(data => {
            if (uploadPrompt) uploadPrompt.style.display = 'flex';
            if (uploadLoading) uploadLoading.style.display = 'none';

            if (!data.success) {
                showUploadError(data.error || 'Failed to parse file.');
                return;
            }

            currentParsedData = data;

            // Populate datalists early
            if (data.existing_groups) {
                populateDatalist('upload-groups-datalist', data.existing_groups);
                populateDatalist('existing-groups-list', data.existing_groups);
            }

            // If Group is missing, admin must enter it on upload screen first before transitioning!
            if (data.missing_group) {
                if (uploadGroupPrompt) {
                    uploadGroupPrompt.style.display = 'block';
                    if (uploadGroupInput) {
                        uploadGroupInput.value = '';
                        uploadGroupInput.focus();
                    }
                    uploadGroupPrompt.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }
                if (uploadScreen) uploadScreen.style.display = 'flex';
                if (importScreen) importScreen.style.display = 'none';
                return;
            }

            // Group is present in file: transition immediately to State 2
            if (uploadGroupPrompt) uploadGroupPrompt.style.display = 'none';
            if (uploadScreen) uploadScreen.style.display = 'none';
            if (importScreen) importScreen.style.display = 'block';

            renderImportState2(data);
        })
        .catch(err => {
            if (uploadPrompt) uploadPrompt.style.display = 'flex';
            if (uploadLoading) uploadLoading.style.display = 'none';
            showUploadError('Network error while processing file: ' + err.message);
        });
    }

    function renderImportState2(data) {
        // 1. Update File Card
        if (displayFilename) displayFilename.textContent = data.filename;
        if (displayFilemeta) {
            displayFilemeta.textContent = `${data.total_rows.toLocaleString()} records · ${data.filesize}`;
        }
        if (fileCardBox) fileCardBox.style.display = 'flex';

        // 2. Missing Fields Inspection (Only Group can be missing)
        const isGroupMissing = Boolean(data.missing_group);

        if (missingSection) {
            if (isGroupMissing) {
                missingSection.style.display = 'block';
                if (missingGroupRow) {
                    missingGroupRow.style.display = 'flex';
                    if (groupSourcePill && registrySchema.group_source) {
                        groupSourcePill.textContent = registrySchema.group_source;
                    }
                    if (missingGroupNotice && registrySchema.group_source) {
                        missingGroupNotice.textContent = `The ${registrySchema.group_source} column is not found in the uploaded file.`;
                    }
                }
            } else {
                missingSection.style.display = 'none';
            }
        }

        // Applied badge in preview header
        if (headerGroupApplied) headerGroupApplied.style.display = isGroupMissing ? 'inline' : 'none';

        // Populate existing datalists
        if (data.existing_groups) populateDatalist('existing-groups-list', data.existing_groups);

        // 3. Render 5-row preview table
        updatePreviewTableRows();

        // 4. Reveal Preview and Actions
        if (previewSection) previewSection.style.display = 'block';
        if (actionsStrip) actionsStrip.style.display = 'flex';
    }

    function updatePreviewTableRows() {
        if (!currentParsedData || !previewBody) return;

        const headers = currentParsedData.headers || [];
        const detected = currentParsedData.detected || {};
        const rows = currentParsedData.preview_rows || [];

        const idColIdx = detected.primary_id ? headers.indexOf(detected.primary_id) : -1;
        const nameColIdx = detected.name ? headers.indexOf(detected.name) : -1;
        const groupColIdx = detected.group ? headers.indexOf(detected.group) : -1;
        const subgroupColIdx = detected.subgroup ? headers.indexOf(detected.subgroup) : -1;
        const genderColIdx = detected.gender ? headers.indexOf(detected.gender) : -1;

        const hasSubgroupCol = Boolean(
            (currentParsedData && currentParsedData.has_subgroups !== undefined)
                ? currentParsedData.has_subgroups
                : (
                    registrySchema.subgroup_source && 
                    registrySchema.subgroup_source.trim() && 
                    registrySchema.subgroup_source.toLowerCase() !== '(none)' &&
                    registrySchema.subgroup_source.toLowerCase() !== 'none'
                )
        );

        const appliedGroup = inputDefaultGroup ? inputDefaultGroup.value.trim() : '';
        let tbodyHtml = '';
        rows.forEach(r => {
            const idVal = idColIdx >= 0 && idColIdx < r.length ? r[idColIdx] : '';
            const nameVal = nameColIdx >= 0 && nameColIdx < r.length ? r[nameColIdx] : '';
            
            // Group: file value if present, else applied group
            let grpVal = groupColIdx >= 0 && groupColIdx < r.length && r[groupColIdx] ? r[groupColIdx] : appliedGroup;
            if (!grpVal) grpVal = '—';

            // Subgroup: directly from file
            let subVal = subgroupColIdx >= 0 && subgroupColIdx < r.length && r[subgroupColIdx] ? r[subgroupColIdx] : '—';

            const genderVal = genderColIdx >= 0 && genderColIdx < r.length ? r[genderColIdx] : '—';

            tbodyHtml += `
                <tr>
                    <td>${escapeHtml(idVal)}</td>
                    <td>${escapeHtml(nameVal)}</td>
                    <td>${escapeHtml(grpVal)}</td>
                    ${hasSubgroupCol ? `<td>${escapeHtml(subVal)}</td>` : ''}
                    <td>${escapeHtml(genderVal)}</td>
                </tr>
            `;
        });

        previewBody.innerHTML = tbodyHtml;
    }

    // Live update preview table on missing field input change
    if (inputDefaultGroup) {
        inputDefaultGroup.addEventListener('input', updatePreviewTableRows);
    }

    // Form submission validation
    if (form) {
        form.addEventListener('submit', function (e) {
            clearAlert();

            // Check if Group is missing from file and not filled by user
            const isGroupMissing = currentParsedData && currentParsedData.missing_group;
            if (isGroupMissing) {
                const grpVal = inputDefaultGroup ? inputDefaultGroup.value.trim() : '';
                if (!grpVal) {
                    e.preventDefault();
                    const label = registrySchema.group_source || 'Group';
                    showAlert(`Please enter or select a ${label} value before importing.`);
                    if (inputDefaultGroup) inputDefaultGroup.focus();
                    return false;
                }
            }

            return true;
        });
    }
});
