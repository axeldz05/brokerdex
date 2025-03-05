from django.shortcuts import render, redirect
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib import messages
from .forms import AccountForm, CustomUserForm
from .models import Account

def log_in(request):
    if request.method == "POST":
        form = AuthenticationForm(data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            return redirect("index")
    else:
        form = AuthenticationForm()
    return render(request,'account/login.html',{"form": form})

def register(request):
    if request.method == "POST":
        user_form = CustomUserForm(request.POST)
        acc_form = AccountForm(request.POST)
        if acc_form.is_valid() and user_form.is_valid():
            user = user_form.save()
            acc = acc_form.save(commit=False)
            acc.user = user
            acc.save()
            login(request, user)
            return redirect("index")
    else:
        user_form = CustomUserForm()
        acc_form = AccountForm()
    return render(request,'account/register.html',{"acc_form": acc_form, "user_form": user_form})

@login_required(login_url="accounts/login/")
def log_out(request):
    logout(request)
    return render(request,'index.html')

@login_required(login_url="accounts/login/")
def settings(request):
    section = request.GET.get('section', 'general')
    context = {'section': section}
    if section == "account":
        if request.method == 'POST':
            form = PasswordChangeForm(request.user, request.POST)
            if form.is_valid():
                user = form.save()
                context['password_form'] = form
                update_session_auth_hash(request, user)
                messages.success(request, 'Your password has been updated successfully!')
                return redirect(f"{request.path}?section=account")
            else:
                messages.error(request, 'Please correct the error below.')
        else:
            form = PasswordChangeForm(request.user)
        context['password_form'] = form
    return render(request, 'account/settings.html', context)
