from django.contrib import messages
from django.contrib.auth import (
    login,
    logout,
    update_session_auth_hash,
)
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .forms import (
    AccountAuthenticationForm,
    AccountRegistrationForm,
    AccountUpdateForm,
)

SETTINGS_SECTIONS = {
    "general",
    "password",
    "bank_identifiers",
    "theme",
}
SETTINGS_SECTION_ALIASES = {
    "account": "password",
}


@ratelimit(key="ip", rate="5/m", method="POST", block=True)
@ratelimit(key="post:username", rate="5/m", method="POST", block=True)
def log_in(request):
    if request.method == "POST":
        form = AccountAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("index")
    else:
        form = AccountAuthenticationForm(request)
    return render(request, 'account/login.html', {"form": form})


@ratelimit(key="ip", rate="3/h", method="POST", block=True)
def register(request):
    if request.method == 'POST':
        form = AccountRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, 'Account created successfully!')
            return redirect('index')
    else:
        form = AccountRegistrationForm()
    
    return render(request, 'account/register.html', {'form': form})

@require_POST
@login_required
def log_out(request):
    logout(request)
    return redirect('index')

@login_required
def settings(request):
    requested_section = request.GET.get("section", "general")
    section = SETTINGS_SECTION_ALIASES.get(
        requested_section,
        requested_section,
    )
    known_section = section in SETTINGS_SECTIONS
    if not known_section:
        section = "general"

    context = {'section': section}
    if section == "password":
        if request.method == 'POST':
            form = PasswordChangeForm(request.user, request.POST)
            if form.is_valid():
                user = form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password has been updated successfully!')
                return redirect(f"{request.path}?section=password")
            else:
                messages.error(request, 'Please correct the error below.')
        else:
            form = PasswordChangeForm(request.user)
        context['password_form'] = form
    elif section == "general":
        account = request.user
        if request.method == 'POST' and known_section:
            acc_form = AccountUpdateForm(request.POST, instance=account)
            if acc_form.is_valid():
                acc_form.save()
                messages.success(request, 'Your data has been updated successfully!')
                return redirect(f"{request.path}?section=general")
            else:
                messages.error(request, 'Please correct the error below.')
        else:
            acc_form = AccountUpdateForm(instance=account)
        context['acc_form'] = acc_form
    return render(request, 'account/settings.html', context)
