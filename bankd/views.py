from django.shortcuts import render

def index(request):
    if request.user.is_authenticated:
        return render(request,'home.html',{})
    else:
        return render(request,'index.html',{})
