
from django.conf import settings
from django.http import HttpResponseNotFound,HttpResponseForbidden

import os

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
        
        if domain.endswith(settings.DEFAULT_DOMAIN):
            
            domainparts = domain.split(".")
            try:
                request.domain = RVDomain.objects.get(name=domainparts[0])
            except:
                return HttpResponseNotFound()
        else:
            try:
                request.domain = RVDomain.objects.get(alt_domain=domain)
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
        
        if domain != settings.DEFAULT_DOMAIN:
        
            return HttpResponseNotFound()

        else:
        
            request.domain = RVDomain.objects.get(name=kwargs["domain_name"])
            
            if request.domain.owner != request.user:
                return HttpResponseForbidden()

            request.vals = {"domain":request.domain}
        
            return func(*args,**kwargs)
    
    return _page  
        
        
	
