

from rvsite.models import RVItem, RVMedia
from PIL import Image

import re
import datetime
import json
import requests
from rearvue import utils
from webpreview import webpreview


def fix_twitter_item(itemid):

    itemid = int(itemid)

    dbitem = RVItem.objects.get(id=itemid)

    try:
        mirror_twitter(specific_item=dbitem)
        find_twitter_links(specific_item=dbitem)
        return (True, "Yo!")
    except Exception as ex:
        return (False, str(ex))


def import_archive(service, data):

    for post in data:
        tweet = post["tweet"]

        if not tweet["full_text"].startswith("RT @") and not tweet["full_text"].startswith("@"):
            try:
                item = RVItem.objects.filter(service=service).filter(item_id=tweet["id"])[0]
            except Exception:
                print("NEW!")
                item = RVItem(item_id=tweet["id"], service=service, domain=service.domain)

            item.caption = tweet["full_text"]

            for n in reversed(tweet["entities"]["user_mentions"]):
                # go backwards to avoid trashing things by moving things around
                item.caption = item.caption[:int(n["indices"][0])] + "<a title='{n}' href='https://twitter.com/{u}/'>@{u}</a>".format(u=n["screen_name"], n=n["name"]) + item.caption[int(n["indices"][1]):]

            for u in tweet["entities"]["urls"]:
                if u["expanded_url"].startswith("https://twitter.com/"):
                    # quote tweet
                    item.caption = item.caption.replace(u["url"], "")
                    item.caption += """
                        <blockquote class="twitter-tweet">
                            <a href="{u}"></a>
                        </blockquote>
                    """.format(u=u["expanded_url"])
                else:
                    item.caption = item.caption.replace(u["url"], "<a href='{xu}'>{su}</a>".format(xu=u["expanded_url"], su=u["display_url"]))

            item.datetime_created = datetime.datetime.strptime(tweet["created_at"], '%a %b %d %H:%M:%S %z %Y')
            item.date_created = datetime.date(year=item.datetime_created.year, month=item.datetime_created.month, day=item.datetime_created.day)

            item.remote_url = "https://twitter.com/{username}/status/{id}".format(username=service.username, id=tweet['id'])

            if "media" in tweet["entities"]:
                for m in tweet["entities"]["media"]:
                    if "url" in m:
                        item.caption = item.caption.replace(m["url"], "")

            if '//twitpic.com' in item.caption:
                regex = r"((http|https)://twitpic\.com/(\w+))"
                matches = re.findall(regex, item.caption)
                for m in matches:
                    if "media" not in tweet["entities"]:
                        tweet["entities"]["media"] = []

                    tweet["entities"]["media"].append(
                        {
                            "type": "photo",
                            "media_url_https": m[0],
                        }
                    )
                    item.caption = item.caption.replace(m[0], "")

            item.caption = item.caption.replace("\n", "<br>")

            item.raw_data = json.dumps(tweet)

            if "media" in tweet["entities"] and item.mirror_state == 0:
                item.save()
                # mirror_twitter(specific_item=item)
            else:
                item.mirror_state = 1
                item.save()


def find_twitter_links(specific_item=None):

    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(mirror_state=1).filter(service__type="twitter").filter(service__live=True)[:100]

    for item in queue:

        print("Twitter Linking {}".format(item.id))

        try:
            tweet = json.loads(item.raw_data)

            if "urls" in tweet["entities"]:

                if len(tweet["entities"]["urls"]) == 0:
                    urls = re.findall(r'http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+', item.caption)
                    for u in urls:
                        if not u.startswith("https://twitter.com"):  # things we linked ourselves
                            final = utils.final_destination(u)
                            short = u.split("://")[1]
                            tweet["entities"]["urls"].append({"expanded_url": final})
                            if '>{}</a>'.format(short) not in item.caption:
                                item.caption = item.caption.replace(u, '<a href="{}">{}</a>'.format(final, short))

                try:
                    item.rvlink_set.filter(is_context=False).delete()
                    for u in tweet["entities"]["urls"]:

                        if not u["expanded_url"].startswith("https://twitter.com"):
                            utils.make_link(u["expanded_url"], item)
                except Exception as ex:
                    print(ex)
                    pass

            item.mirror_state = 2
            item.save()
        except Exception as ex:
            print(ex)
            item.mirror_state = 2
            item.save()





def mirror_twitter(specific_item=None):

    if specific_item is not None:
        queue = [specific_item]
    else:
        queue = RVItem.objects.filter(mirror_state=0).filter(service__type="twitter").filter(service__live=True)[:100]

    for item in queue:

        try:
            tweet = json.loads(item.raw_data)

            for m in item.rvmedia_set.all():
                m.delete()

            if "extended_entities" in tweet:
                media_items = tweet["extended_entities"]["media"]
            else:
                media_items = tweet["entities"]["media"]

            for m in media_items:

                rvm = RVMedia()
                rvm.item = item
                rvm.save()

                if m["type"] == "photo":
                    ret = requests.get(m["media_url_https"], timeout=30, verify=False)
                    rvm.media_type = 1

                    if "twitpic" in m["media_url_https"]:
                        page = ret.content.decode("utf-8")
                        page = page.split('<meta name="twitter:image" value="')[1]
                        m["media_url_https"] = page.split('"')[0]
                        ret = requests.get(m["media_url_https"], timeout=30, verify=False)
                        m["media_url_https"] = m["media_url_https"].split("?")[0]

                    ext = m["media_url_https"].split("/")[-1]

                    if ret.ok:
                        output_path = rvm.make_original_path(ext)

                        target_path = utils.make_full_path(output_path)

                        utils.make_folder(target_path)

                        fh = open(target_path,"wb")
                        fh.write(ret.content)
                        fh.close()

                        #for twitter original and priamary are the same
                        rvm.primary_media = rvm.original_media

                        img = Image.open(target_path)

                        ratio = float(img.size[0]) / float(img.size[1])

                        w = 300
                        h = int(300 / ratio)

                        print("resizing thumbnail" , w, h)

                        img = img.resize((w,h),Image.BICUBIC)

                        output_path = rvm.make_thumbnail_path(ext)

                        target_path = utils.make_full_path(output_path)

                        img.save(target_path)

                elif m["type"] in ["animated_gif", "video"]:

                    best = None
                    br = 0
                    for v in m["video_info"]["variants"]:
                        if v["content_type"].startswith("video/"):
                            if best is None:
                                best = v
                            if "bitrate" in v and int(v["bitrate"]) > br:
                                br = int(v["bitrate"])
                                best = v

                    if best is not None:
                        ret = requests.get(best["url"], timeout=30, verify=False)
                        rvm.media_type = 2
                        ext = best["content_type"].split("/")[-1]

                        if ret.ok:
                            output_path = rvm.make_original_path(ext)

                            target_path = utils.make_full_path(output_path)

                            utils.make_folder(target_path)

                            fh = open(target_path, "wb")
                            fh.write(ret.content)
                            fh.close()

                            # for twitter original and priamary are the same
                            rvm.primary_media = rvm.original_media

                            ret = requests.get(m["media_url_https"], timeout=30, verify=False)

                            ext = m["media_url_https"].split(".")[-1]

                            if ret.ok:
                                output_path = rvm.make_thumbnail_path(ext)

                                target_path = utils.make_full_path(output_path)

                                utils.make_folder(target_path)

                                fh = open(target_path, "wb")
                                fh.write(ret.content)
                                fh.close()

                rvm.save()

            item.mirror_state = 1
            item.save()
        except Exception as ex:
            print(ex)
