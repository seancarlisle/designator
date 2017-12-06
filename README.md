# Designator
When designator is running in combination with Designate with Neutron integration, it will check to see if there are any changes in VM creation/deletion and will create or delete appropriate DNS record in Designate.

# Install
1. Place clouds.yaml on bind server in  ~/.config/openstack/clouds.yaml
2. Update clouds.yaml with your cloud info.
3. Set up venv for designator.
4. Install requirements (`pip install -r requirements.txt ` from your venv). Note that designator uses older version of shade.
5. Setup cron job and run wrapper.sh every minure or so.
