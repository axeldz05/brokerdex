from django.shortcuts import render, redirect
from django.contrib.auth.forms import UserCreationForm, AuthenticationForm
from django.contrib.auth import login
from .forms import AccountForm, CustomUserForm

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
