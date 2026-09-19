FROM frappe/bench:latest

WORKDIR /home/frappe/bench

COPY setup.sh /setup.sh
RUN chmod +x /setup.sh

CMD ["/setup.sh"]
