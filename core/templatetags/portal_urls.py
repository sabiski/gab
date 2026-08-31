from django import template

from core.portal_urls import portal_reverse

register = template.Library()


@register.simple_tag(takes_context=True)
def portal_url(context, viewname, *args, **kwargs):
    request = context.get("request")
    return portal_reverse(request, viewname, *args, **kwargs)
