document.querySelectorAll('.needs-validation').forEach(function (form) {
    form.addEventListener('submit', function (event) {
        if (!form.checkValidity()) {
            event.preventDefault();
            event.stopPropagation();
        }
        form.classList.add('was-validated');
    });
});

document.querySelectorAll('.fact-find-progress[data-percent]').forEach(function (el) {
    el.style.width = el.dataset.percent + '%';
});
