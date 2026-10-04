/**
 * ELECTRA UI ENHANCEMENTS — CUSTOM CONTROLS & VALIDATION
 * - Enforces novalidate on all forms and displays custom Electra error messages.
 * - Replaces default HTML select dropdowns with custom Electra themed dropdowns.
 * - Auto-expiring toast messages for notifications.
 */

(function() {
  'use strict';

  // --------------------------------------------------------------------------
  // 1. AUTO-EXPIRING ELECTRA TOAST NOTIFICATIONS
  // --------------------------------------------------------------------------
  function ensureToastContainer() {
    let container = document.getElementById('electra-toast-container');
    if (!container) {
      container = document.createElement('div');
      container.id = 'electra-toast-container';
      container.className = 'electra-toast-container';
      document.body.appendChild(container);
    }
    return container;
  }

  window.showElectraToast = function(message, type = 'info', duration = 4000) {
    const container = ensureToastContainer();
    const toast = document.createElement('div');
    const toastType = (type === 'error' || type === 'danger') ? 'error' : (type === 'success' ? 'success' : 'info');
    toast.className = `electra-toast electra-toast--${toastType}`;

    toast.innerHTML = `
      <span class="electra-toast-dot"></span>
      <span class="electra-toast-content">${message}</span>
      <button type="button" class="electra-toast-close" aria-label="Dismiss">
        <svg viewBox="0 0 24 24"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
    `;

    const closeBtn = toast.querySelector('.electra-toast-close');
    let timer = null;

    function dismiss() {
      if (timer) clearTimeout(timer);
      toast.classList.add('is-leaving');
      setTimeout(() => {
        if (toast.parentNode) toast.parentNode.removeChild(toast);
      }, 260);
    }

    closeBtn.addEventListener('click', dismiss);
    if (duration > 0) {
      timer = setTimeout(dismiss, duration);
    }

    container.appendChild(toast);
  };

  // Convert existing Django messages into Electra toasts on DOMContentLoaded
  function initDjangoMessages() {
    // Check for message elements that templates might have rendered
    const messageElements = document.querySelectorAll('.registry-msg, .import-msg, .popover-msg, [data-django-message]');
    messageElements.forEach(el => {
      const typeStr = (el.dataset.type || '').toLowerCase();
      const isError = el.classList.contains('registry-msg--error') || 
                      el.classList.contains('import-msg--error') || 
                      el.classList.contains('popover-msg--error') ||
                      typeStr.includes('error') ||
                      typeStr.includes('danger') ||
                      typeStr.includes('warning');
      const text = el.textContent.trim();
      if (text) {
        showElectraToast(text, isError ? 'error' : 'success', 5000);
      }
      // Hide the static raw container to keep UI clean
      el.style.display = 'none';
    });
  }

  // --------------------------------------------------------------------------
  // ELECTRA CUSTOM CONFIRMATION MODAL & ALERT (Replaces native browser confirm/alert)
  // --------------------------------------------------------------------------
  window.electraConfirm = function(arg1 = {}, arg2, arg3, arg4) {
    let opts = {};
    if (typeof arg1 === 'object' && arg1 !== null) {
      opts = arg1;
    } else {
      opts = {
        title: arg1 || 'Confirm Action',
        message: arg2 || 'Are you sure you want to proceed?',
        confirmText: typeof arg3 === 'string' ? arg3 : 'Delete',
        cancelText: 'Cancel',
        isDanger: true,
        onConfirm: typeof arg3 === 'function' ? arg3 : (typeof arg4 === 'function' ? arg4 : null),
      };
    }

    const {
      title = 'Confirm Action',
      message = 'Are you sure you want to proceed?',
      confirmText = 'Confirm',
      cancelText = 'Cancel',
      isDanger = false,
      onConfirm = null,
    } = opts;

    return new Promise((resolve) => {
      let modal = document.getElementById('electra-confirm-modal');
      if (!modal) {
        modal = document.createElement('div');
        modal.id = 'electra-confirm-modal';
        modal.className = 'electra-confirm-backdrop';
        modal.innerHTML = `
          <div class="electra-confirm-card" role="dialog" aria-modal="true">
            <h3 class="electra-confirm-title" id="electra-confirm-title"></h3>
            <p class="electra-confirm-desc" id="electra-confirm-desc"></p>
            <div class="electra-confirm-actions">
              <button type="button" class="electra-confirm-btn electra-confirm-cancel" id="electra-confirm-cancel"></button>
              <button type="button" class="electra-confirm-btn electra-confirm-submit" id="electra-confirm-submit"></button>
            </div>
          </div>
        `;
        document.body.appendChild(modal);
      }

      const titleEl = document.getElementById('electra-confirm-title');
      const descEl = document.getElementById('electra-confirm-desc');
      const cancelBtn = document.getElementById('electra-confirm-cancel');
      const submitBtn = document.getElementById('electra-confirm-submit');

      titleEl.textContent = title;
      descEl.textContent = message;
      cancelBtn.textContent = cancelText;
      submitBtn.textContent = confirmText;

      if (isDanger) {
        submitBtn.classList.add('is-danger');
      } else {
        submitBtn.classList.remove('is-danger');
      }

      function close(confirmed) {
        modal.classList.remove('is-active');
        cleanup();
        if (confirmed && typeof onConfirm === 'function') {
          onConfirm();
        }
        resolve(confirmed);
      }

      function onKey(e) {
        if (e.key === 'Escape') close(false);
      }

      function onBackdrop(e) {
        if (e.target === modal) close(false);
      }

      function cleanup() {
        cancelBtn.removeEventListener('click', handleCancel);
        submitBtn.removeEventListener('click', handleSubmit);
        modal.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKey);
      }

      function handleCancel() { close(false); }
      function handleSubmit() { close(true); }

      cancelBtn.addEventListener('click', handleCancel);
      submitBtn.addEventListener('click', handleSubmit);
      modal.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKey);

      modal.classList.add('is-active');
      submitBtn.focus();
    });
  };

  window.electraConfirmSubmit = function(event, form, title, message, isDanger = true) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    window.electraConfirm({
      title: title || 'Are you sure?',
      message: message || 'This action cannot be undone.',
      confirmText: 'Delete',
      cancelText: 'Cancel',
      isDanger: isDanger,
      onConfirm: () => {
        form.submit();
      }
    });
    return false;
  };

  // --------------------------------------------------------------------------
  // 2. CUSTOM ELECTRA FORM VALIDATION (No HTML default validation bubbles)
  // --------------------------------------------------------------------------
  function clearFieldError(input) {
    input.classList.remove('has-error');
    // If the input was enhanced by a custom dropdown, update its trigger too
    const dropdownWrap = input.closest('.electra-dropdown-wrap');
    if (dropdownWrap) {
      const trigger = dropdownWrap.querySelector('.electra-dropdown-trigger');
      if (trigger) trigger.classList.remove('has-error');
    }

    const parent = input.closest('.registry-form-group, .auth-field, .popover-field, .config-field') || input.parentElement;
    if (parent) {
      const existingError = parent.querySelector('.electra-field-error');
      if (existingError) {
        existingError.remove();
      }
    }
  }

  function showFieldError(input, message) {
    clearFieldError(input);
    input.classList.add('has-error');

    // If input is wrapped in a custom dropdown, highlight trigger
    const dropdownWrap = input.closest('.electra-dropdown-wrap');
    if (dropdownWrap) {
      const trigger = dropdownWrap.querySelector('.electra-dropdown-trigger');
      if (trigger) trigger.classList.add('has-error');
    }

    const errorEl = document.createElement('div');
    errorEl.className = 'electra-field-error';
    errorEl.innerHTML = `
      <svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="10"></circle><line x1="12" y1="8" x2="12" y2="12"></line><line x1="12" y1="16" x2="12.01" y2="16"></line></svg>
      <span>${message}</span>
    `;

    const parent = input.closest('.registry-form-group, .auth-field, .popover-field, .config-field') || input.parentElement;
    if (parent) {
      parent.appendChild(errorEl);
    }
  }

  function validateInput(input) {
    if (input.disabled || input.type === 'hidden' || input.type === 'submit' || input.type === 'button') {
      return true;
    }

    // Required check
    if (input.required) {
      if (input.type === 'checkbox' && !input.checked) {
        showFieldError(input, 'This checkbox is required.');
        return false;
      }
      if (!input.value || !input.value.trim()) {
        const label = input.getAttribute('placeholder') || 'This field';
        showFieldError(input, `${label} is required.`);
        return false;
      }
    }

    // Email format
    if (input.type === 'email' && input.value.trim()) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(input.value.trim())) {
        showFieldError(input, 'Please enter a valid email address.');
        return false;
      }
    }

    // Password confirmation check if confirm_password
    if (input.name === 'confirm_password' && input.value) {
      const form = input.form;
      const pass = form ? form.querySelector('input[name="password"], input[name="new_password"]') : null;
      if (pass && pass.value !== input.value) {
        showFieldError(input, 'Passwords do not match.');
        return false;
      }
    }

    // Minimum length check
    const minLength = input.getAttribute('minlength');
    if (minLength && input.value && input.value.length < parseInt(minLength, 10)) {
      showFieldError(input, `Must be at least ${minLength} characters.`);
      return false;
    }

    clearFieldError(input);
    return true;
  }

  function setupFormValidation() {
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
      // Suppress native HTML bubbles everywhere
      form.setAttribute('novalidate', 'true');

      form.addEventListener('submit', function(e) {
        let isFormValid = true;
        let firstInvalid = null;

        const inputs = form.querySelectorAll('input, select, textarea');
        inputs.forEach(input => {
          if (!validateInput(input)) {
            isFormValid = false;
            if (!firstInvalid) firstInvalid = input;
          }
        });

        if (!isFormValid) {
          e.preventDefault();
          e.stopPropagation();
          if (firstInvalid) {
            firstInvalid.focus();
            showElectraToast('Please check the highlighted fields.', 'error', 3000);
          }
        }
      });

      // Clear errors on input
      form.addEventListener('input', function(e) {
        if (e.target && e.target.classList.contains('has-error')) {
          validateInput(e.target);
        }
      });
      form.addEventListener('change', function(e) {
        if (e.target && e.target.classList.contains('has-error')) {
          validateInput(e.target);
        }
      });
    });
  }

  // --------------------------------------------------------------------------
  // 3. CUSTOM ELECTRA THEMED DROPDOWN (Replaces raw native selects)
  // --------------------------------------------------------------------------
  window.enhanceElectraSelect = function(selectEl) {
    if (!selectEl || selectEl.dataset.electraEnhanced === 'true') return;
    selectEl.dataset.electraEnhanced = 'true';

    // Hide original select visually but keep in DOM for forms/accessibility
    selectEl.style.position = 'absolute';
    selectEl.style.opacity = '0';
    selectEl.style.pointerEvents = 'none';
    selectEl.style.width = '0';
    selectEl.style.height = '0';

    const isFullWidth = selectEl.classList.contains('full-width') || 
                        selectEl.classList.contains('registry-form-select') || 
                        selectEl.classList.contains('config-select');

    const wrapper = document.createElement('div');
    wrapper.className = `electra-dropdown-wrap ${isFullWidth ? 'full-width' : ''}`;

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'electra-dropdown-trigger';
    trigger.setAttribute('aria-haspopup', 'listbox');
    trigger.setAttribute('aria-expanded', 'false');

    const triggerText = document.createElement('span');
    triggerText.className = 'electra-dropdown-text';
    const selectedOption = selectEl.options[selectEl.selectedIndex] || selectEl.options[0];
    triggerText.textContent = selectedOption ? selectedOption.text : 'Select...';

    const arrow = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    arrow.setAttribute('class', 'electra-dropdown-arrow');
    arrow.setAttribute('viewBox', '0 0 24 24');
    arrow.innerHTML = '<polyline points="6 9 12 15 18 9"></polyline>';

    trigger.appendChild(triggerText);
    trigger.appendChild(arrow);
    wrapper.appendChild(trigger);

    const menu = document.createElement('div');
    menu.className = 'electra-dropdown-menu';
    menu.setAttribute('role', 'listbox');

    function buildOptions() {
      menu.innerHTML = '';
      Array.from(selectEl.options).forEach((opt, idx) => {
        const item = document.createElement('div');
        item.className = `electra-dropdown-item ${opt.selected ? 'is-selected' : ''}`;
        item.setAttribute('role', 'option');
        item.setAttribute('aria-selected', opt.selected ? 'true' : 'false');
        item.dataset.value = opt.value;
        item.dataset.index = idx;

        item.innerHTML = `
          <span>${opt.text}</span>
          <svg class="electra-dropdown-check" viewBox="0 0 24 24"><polyline points="20 6 9 17 4 12"></polyline></svg>
        `;

        item.addEventListener('click', function(e) {
          e.stopPropagation();
          selectEl.selectedIndex = idx;
          triggerText.textContent = opt.text;
          
          menu.querySelectorAll('.electra-dropdown-item').forEach(i => i.classList.remove('is-selected'));
          item.classList.add('is-selected');

          wrapper.classList.remove('is-open');
          trigger.setAttribute('aria-expanded', 'false');

          // Dispatch native change event
          const event = new Event('change', { bubbles: true });
          selectEl.dispatchEvent(event);

          clearFieldError(selectEl);
        });

        menu.appendChild(item);
      });
    }

    buildOptions();
    wrapper.appendChild(menu);

    // Insert wrapper before the select element and move select inside wrapper
    selectEl.parentNode.insertBefore(wrapper, selectEl);
    wrapper.appendChild(selectEl);

    // Toggle dropdown open/close
    trigger.addEventListener('click', function(e) {
      e.preventDefault();
      e.stopPropagation();
      const isOpen = wrapper.classList.contains('is-open');
      document.querySelectorAll('.electra-dropdown-wrap.is-open').forEach(w => {
        if (w !== wrapper) {
          w.classList.remove('is-open');
          const t = w.querySelector('.electra-dropdown-trigger');
          if (t) t.setAttribute('aria-expanded', 'false');
        }
      });

      if (!isOpen) {
        buildOptions(); // Refresh in case options changed
        wrapper.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
      } else {
        wrapper.classList.remove('is-open');
        trigger.setAttribute('aria-expanded', 'false');
      }
    });

    // Listen to changes on underlying select (e.g. if updated via JS)
    selectEl.addEventListener('change', function() {
      const curOpt = selectEl.options[selectEl.selectedIndex];
      if (curOpt) {
        triggerText.textContent = curOpt.text;
        menu.querySelectorAll('.electra-dropdown-item').forEach((item, idx) => {
          if (idx === selectEl.selectedIndex) {
            item.classList.add('is-selected');
          } else {
            item.classList.remove('is-selected');
          }
        });
      }
    });

    selectEl.refreshElectra = buildOptions;
  };

  window.refreshElectraSelect = function(selectEl) {
    if (selectEl && typeof selectEl.refreshElectra === 'function') {
      selectEl.refreshElectra();
      const curOpt = selectEl.options[selectEl.selectedIndex];
      const wrap = selectEl.closest('.electra-dropdown-wrap');
      if (wrap && curOpt) {
        const textSpan = wrap.querySelector('.electra-dropdown-text');
        if (textSpan) textSpan.textContent = curOpt.text;
      }
    }
  };

  function initElectraDropdowns() {
    // Enhance all selects in registry and import forms
    const selects = document.querySelectorAll('select.electra-select, select.registry-select, select.registry-form-select, select.config-select');
    selects.forEach(select => {
      enhanceElectraSelect(select);
    });
  }

  // Close any open dropdowns when clicking outside
  document.addEventListener('click', function(e) {
    if (!e.target.closest('.electra-dropdown-wrap')) {
      document.querySelectorAll('.electra-dropdown-wrap.is-open').forEach(w => {
        w.classList.remove('is-open');
        const trigger = w.querySelector('.electra-dropdown-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      });
    }
  });

  // Close on Escape
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.electra-dropdown-wrap.is-open').forEach(w => {
        w.classList.remove('is-open');
        const trigger = w.querySelector('.electra-dropdown-trigger');
        if (trigger) trigger.setAttribute('aria-expanded', 'false');
      });
    }
  });

  // Initialize on DOM ready
  document.addEventListener('DOMContentLoaded', function() {
    setupFormValidation();
    initElectraDropdowns();
    initDjangoMessages();
  });

})();
