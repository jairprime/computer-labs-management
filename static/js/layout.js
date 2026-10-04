const bntDelete = document.querySelectorAll('.btn-delete');

if(bntDelete) {
    const btnArray = Array.from(bntDelete);
    btnArray.forEach((btn) => {
        btn.addEventListener('click', (e) => {
            if(!confirm('¿Estás seguro de que deseas eliminarlo?')) {
                e.preventDefault();
            }
        });
    });
}