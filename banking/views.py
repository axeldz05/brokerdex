from banking import models
from django.shortcuts import render

def index(request):
    return render(request,'banking/index.html',{})

def customers(request):
    customers_list = models.UserExtension.objects.all()
    return render(request,'banking/customers.html',{'customers':customers_list,})

def customers_search(request):
    query = request.GET.get('query')
    customers_list = models.UserExtension.objects.filter(user__first_name__contains=query)
    customers_list2 = models.UserExtension.objects.filter(user__last_name__contains=query)
    customers_list = customers_list.union(customers_list2)
    return render(request,'banking/customers.html',{'customers':customers_list,})
