from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from pathlib import Path

from django.conf import settings
from django.core.files import File

from accounts.models import PartnerProfile, User
from catalog.models import Category, Medicine, PharmacyStock
from core.models import Advertisement, PharmacistTip, SiteHero
from pharmacies.models import Pharmacy, PharmacyEmployee, EmployeeShift
from core.pharmacy_access import ensure_owner_membership
from payments.models import InsuranceProvider


class Command(BaseCommand):
    help = "Charge des données de démo Gab'Pharma (Libreville)"

    def handle(self, *args, **options):
        admin, created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@gabpharma.ga",
                "first_name": "Karld",
                "last_name": "Buanga",
                "role": User.Role.SUPERADMIN,
                "status": User.Status.ACTIVE,
                "is_staff": True,
                "is_superuser": True,
                "city": "Libreville",
                "district": "PK5",
            },
        )
        if created:
            admin.set_password("admin123")
            admin.save()
            self.stdout.write("Superadmin créé : admin / admin123")

        client, created = User.objects.get_or_create(
            username="patient",
            defaults={
                "email": "patient@example.ga",
                "first_name": "Amina",
                "last_name": "Nzoghe",
                "role": User.Role.CLIENT,
                "status": User.Status.ACTIVE,
                "city": "Libreville",
                "district": "Akanda",
                "phone": "+241077000001",
                "loyalty_points": 120,
            },
        )
        if created:
            client.set_password("patient123")
            client.save()

        role_accounts = [
            ("pharmacien", User.Role.PHARMACIST, "Paul", "Mba", "pharma123"),
            ("livreur", User.Role.COURIER, "Jean", "Obiang", "livreur123"),
            ("autorite", User.Role.AUTHORITY, "Dr Claire", "Essono", "autorite123"),
            ("support", User.Role.SUPPORT, "Marie", "Ndong", "support123"),
        ]
        for username, role, first, last, pwd in role_accounts:
            u, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "email": f"{username}@gabpharma.ga",
                    "first_name": first,
                    "last_name": last,
                    "role": role,
                    "status": User.Status.ACTIVE,
                    "city": "Libreville",
                    "is_staff": role in (User.Role.AUTHORITY, User.Role.SUPPORT),
                },
            )
            if created:
                u.set_password(pwd)
                u.save()
                self.stdout.write(f"Compte {role} : {username} / {pwd}")
            else:
                # ensure role/status
                u.role = role
                u.status = User.Status.ACTIVE
                u.save(update_fields=["role", "status"])

        pharmacist = User.objects.filter(username="pharmacien").first()

        categories_data = [
            ("Douleurs & Fièvre", "douleurs-fievre", "head", "purple", "purple", 1),
            ("Toux & Rhume", "toux-rhume", "sneeze", "green", "green", 2),
            ("Digestion", "digestion", "stomach", "orange", "orange", 3),
            ("Soins de la peau", "soins-peau", "lotion", "blue", "blue", 4),
            ("Hygiène & Soins", "hygiene", "soap", "pink", "pink", 5),
            ("Maternité & Bébé", "maternite", "baby", "purple", "purple", 6),
            ("Vitamines & Compléments", "vitamines", "pills", "amber", "amber", 7),
            ("Antibiotiques", "antibiotiques", "pill", "purple", "purple", 8),
        ]
        cats = {}
        for name, slug, icon, color, bg, order in categories_data:
            cat, _ = Category.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "icon": icon,
                    "color": color,
                    "bg_color": bg,
                    "order": order,
                    "is_active": True,
                },
            )
            cats[slug] = cat

        pharmacies_data = [
            {
                "code": "PH-001",
                "name": "Pharmacie Sainte Marie",
                "address": "Avenue de la Révolution",
                "district": "Glass",
                "logo_color": "green",
                "is_24h": True,
                "rating": Decimal("4.8"),
                "review_count": 124,
                "lat": Decimal("0.416200"),
                "lng": Decimal("9.467300"),
            },
            {
                "code": "PH-002",
                "name": "Pharmacie du Port",
                "address": "Boulevard Triomphal",
                "district": "Centre-ville",
                "logo_color": "blue",
                "is_24h": False,
                "is_on_duty": True,
                "rating": Decimal("4.5"),
                "review_count": 89,
                "lat": Decimal("0.390100"),
                "lng": Decimal("9.454200"),
            },
            {
                "code": "PH-003",
                "name": "Pharmacie Akanda Plus",
                "address": "Route de l'aéroport",
                "district": "Akanda",
                "logo_color": "purple",
                "is_24h": False,
                "rating": Decimal("4.6"),
                "review_count": 56,
                "lat": Decimal("0.510000"),
                "lng": Decimal("9.420000"),
            },
            {
                "code": "PH-004",
                "name": "Pharmacie Owendo Santé",
                "address": "Quartier Industriel",
                "district": "Owendo",
                "logo_color": "green",
                "is_24h": True,
                "rating": Decimal("4.3"),
                "review_count": 41,
                "lat": Decimal("0.350000"),
                "lng": Decimal("9.500000"),
            },
        ]

        pharmacies = []
        for data in pharmacies_data:
            ph, _ = Pharmacy.objects.update_or_create(
                code=data["code"],
                defaults={
                    "owner": pharmacist,
                    "name": data["name"],
                    "slug": slugify(data["name"]),
                    "phone": "+241011223344",
                    "email": f"{data['code'].lower()}@gabpharma.ga",
                    "address": data["address"],
                    "city": "Libreville",
                    "district": data["district"],
                    "region": "Estuaire",
                    "latitude": data["lat"],
                    "longitude": data["lng"],
                    "logo_color": data["logo_color"],
                    "status": Pharmacy.Status.ACTIVE,
                    "is_24h": data.get("is_24h", False),
                    "is_on_duty": data.get("is_on_duty", False),
                    "opening_hours": "08:00 – 20:00",
                    "rating": data["rating"],
                    "review_count": data["review_count"],
                    "compliance_score": 92,
                    "subscription_plan": Pharmacy.SubscriptionPlan.PROFESSIONAL,
                    "description": "Pharmacie partenaire Gab'Pharma",
                },
            )
            pharmacies.append(ph)

        medicines_data = [
            ("Doliprane", "1000 mg", "Paracétamol", "Sanofi", "tablet", "douleurs-fievre", True, True, False),
            ("Doliprane", "500 mg", "Paracétamol", "Sanofi", "tablet", "douleurs-fievre", True, False, False),
            ("Amoxicilline", "500 mg", "Amoxicilline", "Biogaran", "capsule", "antibiotiques", False, True, True),
            ("Vitamine C", "1000 mg", "Acide ascorbique", "UPSA", "tablet", "vitamines", True, False, False),
            ("Smecta", "3 g", "Diosmectite", "Ipsen", "sachet", "digestion", True, False, False),
            ("Efferalgan", "1 g", "Paracétamol", "UPSA", "tablet", "douleurs-fievre", False, False, False),
            ("Toplexil", "sirop", "Oxomémazine", "Sanofi", "syrup", "toux-rhume", False, False, False),
            ("Bepanthen", "30 g", "Dexpanthénol", "Bayer", "cream", "soins-peau", True, False, False),
        ]

        stocks_prices = {
            "Doliprane-1000 mg": (1200, 45),
            "Doliprane-500 mg": (800, 60),
            "Amoxicilline-500 mg": (3500, 8),
            "Vitamine C-1000 mg": (2500, 30),
            "Smecta-3 g": (1800, 25),
            "Efferalgan-1 g": (1500, 20),
            "Toplexil-sirop": (4200, 12),
            "Bepanthen-30 g": (5500, 15),
        }

        for name, dosage, dci, lab, form, cat_slug, featured, top, rx in medicines_data:
            slug = slugify(f"{name}-{dosage}")
            med, _ = Medicine.objects.update_or_create(
                slug=slug,
                defaults={
                    "name": name,
                    "dosage": dosage,
                    "dci": dci,
                    "laboratory": lab,
                    "form": form,
                    "category": cats[cat_slug],
                    "is_featured": featured,
                    "is_top": top,
                    "requires_prescription": rx,
                    "description": f"{name} {dosage} — {dci}",
                },
            )
            key = f"{name}-{dosage}"
            price, qty = stocks_prices[key]
            for i, ph in enumerate(pharmacies):
                promo = None
                if featured and i == 0:
                    promo = int(price * 0.85)
                PharmacyStock.objects.update_or_create(
                    pharmacy=ph,
                    medicine=med,
                    defaults={
                        "quantity": max(0, qty - i * 3),
                        "price": price + i * 50,
                        "promotional_price": promo,
                        "low_stock_threshold": 10,
                        "is_visible": True,
                        "lot_number": f"LOT{1000 + i}",
                    },
                )

        self.stdout.write(self.style.SUCCESS("Données de démo chargées avec succès."))
        self.stdout.write(
            "Comptes : admin/admin123 · patient/patient123 · pharmacien/pharma123 · "
            "livreur/livreur123 · autorite/autorite123 · support/support123 · "
            "cnamgs/cnamgs123 (assurance → /espace/assurance/)"
        )

        # Contenu CMS accueil (images démo dans static/demo — à remplacer plus tard)
        demo_dir = Path(settings.BASE_DIR) / "static" / "demo"
        hero = SiteHero.get_solo()
        hero_file = demo_dir / "hero-demo.jpg"
        if hero_file.exists() and not hero.hero_image:
            with hero_file.open("rb") as f:
                hero.hero_image.save("hero-demo.jpg", File(f), save=True)
            self.stdout.write("Hero démo (pharmacie / patients) chargé.")

        demo_ads = [
            ("Airtel Money", "ad-airtel.jpg", Advertisement.Placement.HOME_TOP, "https://www.airtel.ga/", 30),
            ("Moov Money", "ad-moov.jpg", Advertisement.Placement.HOME_MID, "https://www.moov-africa.ga/", 20),
            ("BCEG", "ad-bceg.jpg", Advertisement.Placement.HOME_BOTTOM, "https://www.bceg.ga/", 10),
        ]
        for title, filename, placement, link, priority in demo_ads:
            path = demo_dir / filename
            if not path.exists():
                continue
            ad, created = Advertisement.objects.get_or_create(
                title=title,
                defaults={
                    "link_url": link,
                    "placement": placement,
                    "is_active": True,
                    "priority": priority,
                },
            )
            if created or not ad.image:
                with path.open("rb") as f:
                    ad.image.save(filename, File(f), save=True)
                ad.link_url = link
                ad.placement = placement
                ad.is_active = True
                ad.priority = priority
                ad.save()
        if any((demo_dir / f).exists() for _, f, *_ in demo_ads):
            self.stdout.write("Publicités démo Airtel / Moov / BCEG chargées.")

        if not PharmacistTip.objects.exists() and pharmacies:
            tips_data = [
                ("Prévenir le paludisme en saison des pluies", "Santé · Prévention", "Les gestes essentiels et les traitements de première intention à connaître.", "coronavirus"),
                ("Trousse de voyage : les indispensables", "Voyage", "Antalgiques, antidiarrhéiques, réhydratation — la checklist du pharmacien.", "luggage"),
                ("Automédication responsable", "Bien-être", "Quand consulter, quand traiter — et comment éviter les interactions.", "health_and_safety"),
            ]
            for title, cat, excerpt, icon in tips_data:
                PharmacistTip.objects.create(
                    pharmacy=pharmacies[0],
                    title=title,
                    category=cat,
                    excerpt=excerpt,
                    icon=icon,
                    is_published=True,
                )
            self.stdout.write("Conseils pharmacien de démo créés.")

        for name, code, rate in (
            ("CNAMGS", "CNAMGS", 80),
            ("NSIA", "NSIA", 70),
            ("Ascoma", "ASCOMA", 75),
        ):
            InsuranceProvider.objects.get_or_create(
                code=code,
                defaults={"name": name, "coverage_rate": rate, "is_active": True},
            )
        from core.insurer_subscription import ensure_provider_plans

        for provider in InsuranceProvider.objects.filter(is_active=True):
            ensure_provider_plans(provider)
        self.stdout.write("Assureurs démo (CNAMGS / NSIA / Ascoma) prêts.")

        cnamgs_provider = InsuranceProvider.objects.filter(code="CNAMGS").first()
        if cnamgs_provider:
            insurer_user, created = User.objects.get_or_create(
                username="cnamgs",
                defaults={
                    "email": "cnamgs@gabpharma.ga",
                    "first_name": "Admin",
                    "last_name": "CNAMGS",
                    "role": User.Role.PARTNER,
                    "status": User.Status.ACTIVE,
                    "city": "Libreville",
                    "phone": "+241077000099",
                },
            )
            if created:
                insurer_user.set_password("cnamgs123")
                insurer_user.save()
                self.stdout.write("Compte assurance démo : cnamgs / cnamgs123")
            profile, p_created = PartnerProfile.objects.get_or_create(
                user=insurer_user,
                defaults={
                    "partner_type": PartnerProfile.PartnerType.INSURER,
                    "organization_name": "CNAMGS",
                    "acronym": "CNAMGS",
                    "insurance_provider": cnamgs_provider,
                    "validated_at": timezone.now(),
                },
            )
            if not p_created and not profile.insurance_provider_id:
                profile.insurance_provider = cnamgs_provider
                profile.partner_type = PartnerProfile.PartnerType.INSURER
                profile.save(update_fields=["insurance_provider", "partner_type"])

            from accounts.models import PartnerSubscription
            from core.partner_subscription import create_partner_subscription

            if profile.validated_at and not profile.platform_subscriptions.exists():
                create_partner_subscription(
                    profile,
                    PartnerSubscription.Plan.PROFESSIONAL,
                    activate=True,
                    payment_reference="DEMO-SEED",
                    payment_method="transfer",
                )

        if pharmacist and pharmacies:
            main_ph = pharmacies[0]
            ensure_owner_membership(main_ph)
            staff_demo = [
                (
                    "prep1",
                    "Marie",
                    "Obame",
                    PharmacyEmployee.JobRole.PREPARER,
                    "Préparatrice",
                    280000,
                ),
                (
                    "caissier1",
                    "Jean",
                    "Moussavou",
                    PharmacyEmployee.JobRole.CASHIER,
                    "Caissier",
                    220000,
                ),
                (
                    "pharma2",
                    "Claire",
                    "Ngoma",
                    PharmacyEmployee.JobRole.PHARMACIST,
                    "Pharmacienne adjointe",
                    450000,
                ),
            ]
            for username, first, last, role, title, salary in staff_demo:
                u, u_created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "email": f"{username}@gabpharma.ga",
                        "first_name": first,
                        "last_name": last,
                        "role": User.Role.PHARMACIST,
                        "status": User.Status.ACTIVE,
                        "phone": "+24106000000",
                        "city": "Libreville",
                    },
                )
                if u_created:
                    u.set_password("pharma123")
                    u.save(update_fields=["password"])
                PharmacyEmployee.objects.get_or_create(
                    pharmacy=main_ph,
                    user=u,
                    defaults={
                        "employee_code": f"{main_ph.code}-{username}".upper(),
                        "job_role": role,
                        "job_title": title,
                        "base_salary": salary,
                        "contract_type": PharmacyEmployee.ContractType.CDI,
                    },
                )
            self.stdout.write("Personnel ERP démo (Sainte Marie) : titulaire + 3 collaborateurs.")
