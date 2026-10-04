/**
 * Electra Authentication & Setup Page Helper
 * Password visibility toggling and history navigation reload.
 */

document.addEventListener('DOMContentLoaded', function() {
    // Password toggle functionality
    const toggleButtons = document.querySelectorAll('.auth-password-toggle');

    toggleButtons.forEach(button => {
        button.addEventListener('click', function(e) {
            e.preventDefault();
            const targetId = this.getAttribute('data-target');
            const input = document.getElementById(targetId);
            if (!input) return;

            const isPassword = input.type === 'password';
            input.type = isPassword ? 'text' : 'password';

            // Swap icon SVGs
            const eyeOpen = this.querySelector('.icon-eye-open');
            const eyeClosed = this.querySelector('.icon-eye-closed');
            if (eyeOpen && eyeClosed) {
                if (isPassword) {
                    eyeOpen.style.display = 'none';
                    eyeClosed.style.display = 'block';
                    this.setAttribute('aria-label', 'Hide password');
                } else {
                    eyeOpen.style.display = 'block';
                    eyeClosed.style.display = 'none';
                    this.setAttribute('aria-label', 'Show password');
                }
            }
        });
    });

    // Force page reload on back/forward cache navigation to prevent stale state
    window.addEventListener('pageshow', function(event) {
        if (event.persisted) {
            window.location.reload();
        }
    });
});
