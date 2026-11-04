from abc import abstractmethod

class BaseProvider:
        @abstractmethod
        def send(self,credentials,payload):
                # credentials -> API KEY, token information coming from Channel model
                #payload -> to, subject, body information coming from Action Model
                pass

        def simulate(self,credentials,payload):
                """
                What this provider *would* send, without sending it.

                A dry run (RunMode.TEST) must not reach the outside world: its whole
                purpose is to let an author see the rendered payload before a real
                customer does. So the default answer is the payload itself - no
                credential is read, no socket is opened - and a provider that has
                something more useful to say (a normalised phone number, a resolved
                recipient) can override this.

                Deliberately NOT abstract: every existing provider keeps working
                unchanged, and a new one only has to implement `send`.
                """
                return {'would_send' : payload}

        def dispatch(self,credentials,payload,mode = 'live'):
                """
                Run this provider in the mode the caller asked for.

                One method instead of an `if mode == 'test'` in every caller: the
                branch between "really send" and "only say what would be sent" then
                has exactly one home per provider, and a provider with a special
                simulation (never a `send` with a `dry_run` flag on the request, which
                some vendor APIs treat as a real call) can own it.
                """
                if mode == 'test':
                        return self.simulate(credentials,payload)

                return self.send(credentials,payload)
