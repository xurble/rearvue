
from django.conf import settings
from django.http import HttpResponseNotFound,HttpResponseForbidden

import os
import random

from rvsite.models import RVDomain

def make_full_path(local_path):
    
    return os.path.join(settings.DATA_STORE, local_path)
    
    
def make_folder(full_path_to_file):

    folder = os.path.dirname(full_path_to_file)
    
    if not os.path.exists(folder):
        os.makedirs(folder)
        
        
def page(func):

    def _page(*args,**kwargs):
    
        request = args[0]
        
        domain = request.META["HTTP_HOST"]
        
        try:
            request.domain = RVDomain.objects.get(name=domain)
        except:
            return HttpResponseNotFound()

        request.vals = {}
        request.vals["domain"] = request.domain
        request.vals["year_range"] = list(range(request.domain.min_year,request.domain.max_year+1)) 

        
        return func(*args,**kwargs)
    
    return _page        
        
        
def admin_page(func):

    def _page(*args,**kwargs):
    
        request = args[0]

        domain = request.META["HTTP_HOST"]
        
        try:
            request.domain = RVDomain.objects.get(name=domain)
        except:
            return HttpResponseNotFound()

        
        if not request.user.is_superuser:
            return HttpResponseForbidden()


        request.vals = {}
        request.vals["domain"] = request.domain
    
        return func(*args,**kwargs)
    
    return _page  
    
    
def sample_of(source_list, requirement): 

    if requirement > len(source_list):
        requirement = len(source_list)

    return random.sample(source_list, requirement)
        
        
	
